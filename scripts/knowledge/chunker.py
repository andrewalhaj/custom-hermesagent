"""Paragraph-overlap markdown chunker with heading-context tracking, plus the
synthetic path/heading prefix ref_chunks embeds with each chunk."""
import os
import re

CHUNK_TARGET_CHARS = 800   # target chunk size for overlap chunker
CHUNK_MAX_CHARS = 1500     # hard cap — split paragraph internally if needed


def _split_paragraphs(text):
    """Split text into paragraphs on double-newlines. Preserve heading lines."""
    # Split on \n\n+, keeping separators attached to the following paragraph
    paras = re.split(r'\n\n+', text)
    return [p.strip() for p in paras if p.strip()]

def _heading_stack(lines, current_stack=None):
    """Walk lines, maintain a heading hierarchy stack. Returns list of (level, heading)."""
    stack = current_stack or []
    for line in lines:
        m = re.match(r'^(#{1,6})\s+(.+)', line)
        if m:
            level = len(m.group(1))
            heading = m.group(2).strip()
            # Pop headings at same or deeper level
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
    return stack

def _heading_breadcrumb(heading_stack):
    """Format heading stack as 'Doc Title > Section > Subsection'."""
    if not heading_stack:
        return ''
    return ' > '.join(h[1] for h in heading_stack)

def _leaf_heading_line(heading_stack):
    """Reconstruct the markdown heading line for the deepest (leaf) heading,
    e.g. (2, 'Manifest') -> '## Manifest'. Returns '' if no heading."""
    if not heading_stack:
        return ''
    level, heading = heading_stack[-1]
    return ('#' * int(level)) + ' ' + heading

def _normalize_chunk_heading(body, heading_stack):
    """Ensure a chunk body starts with its leaf heading line.

    The overlap chunker (1-paragraph overlap) can start a chunk on the lone
    content paragraph of a short section, producing a headerless twin of the
    previous chunk that differs ONLY by the heading line. Exact-hash dedup
    (body_hash) misses these. Prepending the leaf heading makes such twins
    byte-identical to the heading-bearing chunk, so body_hash collapses them.
    No-op when the body already leads with that heading line."""
    hl = _leaf_heading_line(heading_stack)
    if not hl:
        return body
    first_line = body.lstrip().split('\n', 1)[0].strip()
    if first_line == hl:
        return body
    return f"{hl}\n\n{body}"

def _hard_split(piece):
    """Split a piece longer than CHUNK_MAX_CHARS (tables, logs, code: no sentence
    punctuation) on line breaks, then at CHUNK_TARGET_CHARS as a last resort, so
    no chunk can exceed the hard cap. Pieces within the cap are returned as-is."""
    if len(piece) <= CHUNK_MAX_CHARS:
        return [piece]
    out, cur = [], ''
    for line in piece.split('\n'):
        while len(line) > CHUNK_TARGET_CHARS:
            if cur:
                out.append(cur)
                cur = ''
            out.append(line[:CHUNK_TARGET_CHARS])
            line = line[CHUNK_TARGET_CHARS:]
        if cur and len(cur) + 1 + len(line) > CHUNK_TARGET_CHARS:
            out.append(cur)
            cur = line
        else:
            cur = (cur + '\n' + line) if cur else line
    if cur:
        out.append(cur)
    return out


def chunk_overlap(text, source_path=''):
    """Overlap-based paragraph chunking. Returns list of (chunk_body, heading_context, paragraphs).

    Chunks are ~CHUNK_TARGET_CHARS with 1-paragraph overlap between consecutive chunks.
    Each chunk tracks which heading(s) it falls under.
    """
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    # Build heading context for each paragraph
    # Walk the text, tracking heading stack, assign to each paragraph
    lines = text.split('\n')
    para_headings = []
    stack = []
    para_idx = 0
    # Reconstruct which lines belong to which paragraph
    para_start_lines = {}
    pos = 0
    for i, para in enumerate(paragraphs):
        # Find where this paragraph starts in the original text
        idx = text.index(para, pos) if para in text[pos:] else pos
        # Count newlines before it to get line number
        line_no = text[:idx].count('\n')
        para_start_lines[i] = line_no
        pos = idx + len(para)

    # Now assign headings to each paragraph
    for i in range(len(paragraphs)):
        line_no = para_start_lines[i]
        # Rebuild heading stack up to this paragraph
        stack = []
        for j, line in enumerate(lines[:line_no + 1]):
            m = re.match(r'^(#{1,6})\s+(.+)', line)
            if m:
                level = len(m.group(1))
                heading = m.group(2).strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading))
        para_headings.append(list(stack))  # copy

    # Build chunks with overlap
    chunks = []
    i = 0
    while i < len(paragraphs):
        chunk_paras = [paragraphs[i]]
        chunk_len = len(paragraphs[i])
        # Use the heading context from the FIRST paragraph in this chunk.
        # This is the most stable anchor — unlike the last paragraph,
        # it won't get reset by a high-level heading (e.g. "## Cron Jobs"
        # resetting the hierarchy from "Infra > Routing > DB Topology").
        heading_stack = para_headings[i]
        j = i + 1
        while j < len(paragraphs) and chunk_len + len(paragraphs[j]) < CHUNK_TARGET_CHARS:
            chunk_paras.append(paragraphs[j])
            chunk_len += len(paragraphs[j])
            j += 1

        body = '\n\n'.join(chunk_paras)
        # Prepend the leaf heading if this chunk starts mid-section (overlap
        # twin). Makes headerless overlap twins byte-identical to their
        # heading-bearing sibling so body_hash dedup collapses them.
        body = _normalize_chunk_heading(body, heading_stack)
        heading_ctx = _heading_breadcrumb(heading_stack)

        # If body exceeds max, split internally by sentences
        if len(body) > CHUNK_MAX_CHARS:
            sentences = []
            for s in re.split(r'(?<=[.!?])\s+', body):
                sentences.extend(_hard_split(s))
            sub_body = ''
            for s in sentences:
                if len(sub_body) + len(s) > CHUNK_TARGET_CHARS and sub_body:
                    chunks.append((sub_body.strip(), heading_ctx, chunk_paras))
                    sub_body = s
                else:
                    sub_body = (sub_body + ' ' + s).strip()
            if sub_body:
                chunks.append((sub_body.strip(), heading_ctx, chunk_paras))
        else:
            chunks.append((body, heading_ctx, chunk_paras))

        # Advance: overlap by 1 paragraph
        i = max(i + 1, j - 1) if j > i + 1 else i + 1

    return chunks


def synthetic_prefix(source_path, heading_context):
    """'This passage is from <file name>: <heading>.' — situates a chunk without an LLM call."""
    fname = os.path.basename(source_path) if source_path else 'unknown'
    fname_clean = fname.replace('.md', '').replace('-', ' ').replace('_', ' ')
    if heading_context:
        return f"This passage is from {fname_clean}: {heading_context}."
    return f"This passage is from {fname_clean}."

import os
import re
import logging
from pathlib import Path

import fitz

logger = logging.getLogger("codewalk")

DOC_EXTENSIONS = {".md", ".pdf", ".txt", ".rst"}

TXT_CHUNK_SIZE = 3000
TXT_CHUNK_OVERLAP = 200

def discover_docs(docs_path: str) -> list[dict]:
    """Walk a directory and find all supported document files.

    Returns:
        [{"file_path": "guides/deploy.md", "absolute_path": "/full/path/...", "ext": ".md"}, ...]

    TEACH: This mirrors scanner.py's scan_directory() pattern —
           returns relative paths + absolute paths so we can:
           - Use relative path as the document ID (like file_path in code chunks)
           - Use absolute path to actually read the file
    """
    docs_path = docs_path.rstrip("/")
    results = []

    for root, _dirs, files in os.walk(docs_path):
        for filename in sorted(files):
            ext = Path(filename).suffix.lower()
            if ext not in DOC_EXTENSIONS:
                continue

            abs_path = os.path.join(root, filename)
            relative_path = os.path.relpath(abs_path, docs_path)

            results.append({
                "file_path": relative_path,
                "absolute_path": abs_path,
                "ext": ext,
            })
    
    logger.info(f"[doc_parser] Found {len(results)} documents in {docs_path}")
    return results

def parse_markdown(file_path: str, relative_path: str) -> list[dict]:
    """Parse a markdown file into chunks split by headings.

    TEACH: Strategy:
      1. Read the entire file
      2. Split on lines that start with # (headings)
      3. Each chunk = one section (heading + content until next heading)
      4. If no headings found, the whole file is one chunk

    Returns:
        [{"text": "## Deploy\nSteps to deploy...",
          "metadata": {"doc_name": "deploy.md", "section": "Deploy",
                       "doc_path": "guides/deploy.md", "chunk_index": 0}}, ...]
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"[doc_parser] Cannot read {file_path}: {e}")
        return []

    doc_name = Path(relative_path).name

    lines = content.split("\n")
    sections = []
    current_heading = "(intro)"
    current_lines = []

    for line in lines:
        # A markdown heading is a line starting with one or more
        # followed by a space. "###Fix" is NOT a heading (no space).
        if re.match(r"^#{1,6}\s+", line):
            # Save previous section if it has content
            if current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    sections.append((current_heading, text))
            # Start new section
            current_heading = re.sub(r"^#+\s+", "", line).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    
    # last section
    if current_lines:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append((current_heading, text))

    chunks = []
    for index, (heading, section) in enumerate(sections):
        if len(section) < 20:
            continue

        chunks.append({
            "text": section,
            "metadata": {
                "doc_name": doc_name,
                "doc_path": relative_path,
                "section": heading,
                "chunk_index": index,
                "source_type": "markdown",
            },
        })

    return chunks


def parse_rst(file_path: str, relative_path: str) -> list[dict]:
    """Parse a reStructuredText file into chunks split by section headings.

    RST headings are lines of text followed (and optionally preceded) by a
    line of repeated punctuation characters: = - ~ ^ " ' + # * ` :
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"[doc_parser] Cannot read {file_path}: {e}")
        return []

    doc_name = Path(relative_path).name
    lines = content.split("\n")
    sections = []
    current_heading = "(intro)"
    current_lines = []
    RST_ADORNMENTS = set("=-~^\"'+#*`:")

    def _is_adornment(line: str) -> bool:
        if not line:
            return False
        first = line[0]
        if first not in RST_ADORNMENTS:
            return False
        return all(c == first for c in line)

    i = 0
    while i < len(lines):
        line = lines[i]

        # Look for underline-style heading: text line followed by adornment line
        if (
            i + 1 < len(lines)
            and line.strip()
            and _is_adornment(lines[i + 1])
        ):
            if current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    sections.append((current_heading, text))
            current_heading = line.strip()
            current_lines = [line]
            # Skip the adornment line; it belongs to the heading, not body.
            i += 2
            continue

        # Look for overline+underline heading: adornment, text, same adornment
        if (
            i + 2 < len(lines)
            and _is_adornment(line)
            and lines[i + 1].strip()
            and _is_adornment(lines[i + 2])
            and lines[i][0] == lines[i + 2][0]
        ):
            if current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    sections.append((current_heading, text))
            current_heading = lines[i + 1].strip()
            current_lines = [lines[i + 1]]
            i += 3
            continue

        current_lines.append(line)
        i += 1

    # last section
    if current_lines:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append((current_heading, text))

    chunks = []
    for index, (heading, section) in enumerate(sections):
        if len(section) < 20:
            continue
        chunks.append({
            "text": section,
            "metadata": {
                "doc_name": doc_name,
                "doc_path": relative_path,
                "section": heading,
                "chunk_index": index,
                "source_type": "rst",
            },
        })

    return chunks


def parse_pdf(file_path: str, relative_path: str) -> list[dict]:
    """Parse a PDF file into chunks — one chunk per page.

    TEACH: Why per-page (not per-heading)?
      PDFs don't have reliable heading structure. PyMuPDF can extract text
      but heading detection requires font-size heuristics (fragile).
      Per-page chunking is simple, reliable, and good enough for most docs.

      If a page is very long (>3000 chars), we could split further,
      but most doc pages are under 2000 chars — fine for embedding.

    Returns:
        [{"text": "Page content...",
          "metadata": {"doc_name": "arch.pdf", "section": "Page 1",
                       "doc_path": "guides/arch.pdf", "page": 1, "chunk_index": 0}}, ...]
    """
    doc_name = Path(relative_path).name

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        logger.warning(f"[doc_parser] Cannot open PDF {file_path}: {e}")
        return []
    
    chunks = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text().strip()

        if len(text) < 20:
            continue

        chunks.append({
            "text": text,
            "metadata": {
                "doc_name": doc_name,
                "doc_path": relative_path,
                "section": f"Page {page_num + 1}",
                "page": page_num + 1,
                "chunk_index": page_num,
                "source_type": "pdf",
            },
        })
    
    doc.close()

    return chunks

def _split_text_by_chars(text: str) -> list[str]:
    """Split text into overlapping windows by character count.

    TEACH: This is a simplified version of RecursiveCharacterTextSplitter.
           We don't import LangChain here to keep doc_knowledge dependency-free.

           How it works:
             text = "ABCDEFGHIJ", chunk_size=4, overlap=1
             chunk 0: "ABCD" (start=0)
             chunk 1: "DEFG" (start=3, overlaps 'D')
             chunk 2: "GHIJ" (start=6, overlaps 'G')

           The overlap ensures no sentence gets cut without context
           in the next chunk.
    """
    if len(text) <= TXT_CHUNK_SIZE:
        return [text]
    
    chunks = []
    start = 0

    while start < len(text):
        end = start + TXT_CHUNK_SIZE
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)

        start += TXT_CHUNK_SIZE - TXT_CHUNK_OVERLAP
    
    return chunks


def parse_text(file_path: str, relative_path: str) -> list[dict]:
    """Parse a plain .txt file — entire file is one chunk.

    TEACH: No structure to split on. The whole file becomes a single chunk.
           If the file is huge (>5000 chars), we truncate to avoid
           embedding model limits (most models cap at 8192 tokens).
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"[doc_parser] Cannot read {file_path}: {e}")
        return []
    
    doc_name = Path(relative_path).name
    text = content.strip()

    if len(text) < 20:
        return []
    
    text_pieces = _split_text_by_chars(text)

    chunks = []

    for index, piece in enumerate(text_pieces):
        section = "(full document)" if len(text_pieces) == 1 else f"Part {index + 1}"
        chunks.append({
            "text": piece,
            "metadata": {
                "doc_name": doc_name,
                "doc_path": relative_path,
                "section": section,
                "chunk_index": index,
                "source_type": "text",
            },
        })

    return chunks
    

def parse_doc(doc_info: dict) -> list[dict]:
    """Route a document to the right parser based on extension.

    TEACH: This is the public API — callers pass a dict from discover_docs()
           and get back a list of chunks. Same pattern as code_parser.parse_file().

    Args:
        doc_info: {"file_path": str, "absolute_path": str, "ext": str}

    Returns:
        List of chunk dicts with "text" and "metadata" keys.
    """
    ext = doc_info["ext"]
    abs_path = doc_info["absolute_path"]
    rel_path = doc_info["file_path"]

    if ext == ".md":
        return parse_markdown(abs_path, rel_path)
    elif ext == ".rst":
        return parse_rst(abs_path, rel_path)
    elif ext == ".pdf":
        return parse_pdf(abs_path, rel_path)
    elif ext == ".txt":
        return parse_text(abs_path, rel_path)
    else:
        return []
    

def parse_all_docs(docs_path: str) -> list[dict]:
    """Discover and parse all documents in a directory.

    TEACH: This is the top-level function — equivalent to pipeline.py's
           chunk_all_files() but for documents instead of code.

    Returns:
        Combined list of all chunks from all documents.
    """
    doc_files = discover_docs(docs_path)
    all_chunks = []

    for doc_info in doc_files:
        chunks = parse_doc(doc_info)
        all_chunks.extend(chunks)

    logger.info(
        f"[doc_parser] Parsed {len(doc_files)} documents → "
        f"{len(all_chunks)} chunks"
    )
    return all_chunks






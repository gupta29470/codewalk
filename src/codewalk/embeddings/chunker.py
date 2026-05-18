"""
=============================================================================
 chunker.py — Code Splitting for Embeddings
=============================================================================

WHAT THIS FILE DOES:
    Splits source code files into smaller pieces ("chunks") that can be
    individually embedded and searched. Each chunk becomes one entry in
    ChromaDB's vector database.

WHY NOT EMBED WHOLE FILES?
    - Embedding models have token limits (typically 512-8192 tokens)
    - A 500-line file would exceed the limit and get truncated
    - Searching works better with focused chunks — "login function" is more
      findable than "the entire auth.py file"
    - Smaller chunks = more precise search results

HOW IT WORKS (TWO STRATEGIES):

    STRATEGY 1: Parser-Based (preferred, for supported languages)
        1. Use tree-sitter to parse the file into an AST
        2. Extract each function/class as its own chunk
        3. Each chunk gets rich metadata: symbol_name, type, line range
        4. Leftover top-level code (imports, constants) → text-split

    STRATEGY 2: Text-Based (fallback, for unsupported languages)
        1. Split text at natural boundaries (blank lines, brackets)
        2. Each chunk is ~1000 characters with 200-char overlap
        3. No symbol metadata (just file_path and chunk_index)

    OVERLAP (200 chars):
        Adjacent chunks share 200 characters at their boundaries.
        This prevents functions from being "cut" mid-sentence.
        If a search matches something at a chunk boundary, the overlap
        ensures the relevant context isn't lost.

REAL-WORLD ANALOGY:
    Like indexing a book. Strategy 1 = index by chapter/section (structured).
    Strategy 2 = cut every 2 pages (mechanical). Strategy 1 gives you
    "Chapter 5: Authentication" which is much more searchable than "pages 89-90".

WHERE IT'S CALLED:
    - pipeline.py → chunk_all_files() during full indexing
    - pipeline.py → chunk_file() during incremental reindex of one file

DEPENDENCIES:
    - code_parser.py: tree-sitter parsing (extract functions/classes)
    - langchain_text_splitters: smart text splitting with overlap
    - config.py: via code_parser for language support info

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

from pathlib import Path
import hashlib         # MD5 hash for change detection
import logging

# LangChain's text splitters know language syntax (Python, JS, etc.)
# They split at natural boundaries: end of function, blank lines, brackets
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language

# parse_file(): tree-sitter → extracts functions/classes with line numbers
# GRAMMAR_MAP: which languages tree-sitter supports
from src.codewalk.analysis.code_parser import parse_file, GRAMMAR_MAP
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")


# =============================================================================
# LANGUAGE_MAP — Our Language Names → LangChain's Language Enum
# =============================================================================
#
# LangChain needs its own Language enum to pick the right splitting strategy.
# "python" → Language.PYTHON tells the splitter to split at def/class boundaries.
# "javascript" → Language.JS tells it to split at function/const boundaries.
#
# Languages NOT in this map get generic text splitting (no syntax awareness).

LANGUAGE_MAP = {
    "python": Language.PYTHON,
    "javascript": Language.JS,
    "typescript": Language.TS,
    "java": Language.JAVA,
    "go": Language.GO,
    "rust": Language.RUST,
    "ruby": Language.RUBY,
    "php": Language.PHP,
    "csharp": Language.CSHARP,
    "cpp": Language.CPP,
    "c": Language.C,
    "kotlin": Language.KOTLIN,
    "swift": Language.SWIFT,
    "html": Language.HTML,
    "markdown": Language.MARKDOWN,
}


# =============================================================================
# Helper Functions
# =============================================================================

def file_hash(content: str) -> str:
    """Generate an MD5 hash of file content for change detection.

    Used during incremental reindex:
      stored_hash = "abc123" (from ChromaDB)
      current_hash = file_hash(read_file()) → "abc123"
      If equal → file unchanged → skip re-embedding (saves time)
    """
    return hashlib.md5(content.encode()).hexdigest()


def read_file_content(file_path: str) -> str:
    """Read a file's text content. Return empty string if it can't be read.

    Gracefully handles:
      - Binary files that can't be decoded as UTF-8 → ""
      - Permission-denied files → ""
    This prevents one unreadable file from crashing the entire pipeline.
    """
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return ""


def get_splitter(language: str, chunk_size: int = 1000, chunk_overlap: int = 200):
    """Get the right text splitter for a language.

    IF LANGUAGE IS KNOWN (in LANGUAGE_MAP):
        Returns a language-aware splitter that knows syntax:
        - Python: splits at 'def ', 'class ', blank lines
        - JS: splits at 'function ', 'const ', '}\n'
        Result: chunks tend to be complete logical blocks

    IF LANGUAGE IS UNKNOWN:
        Returns generic splitter that splits at:
        - Double newlines, single newlines, spaces
        Result: chunks are arbitrary text blocks (still useful)

    Args:
        chunk_size: Target maximum characters per chunk (default 1000)
        chunk_overlap: Characters shared between adjacent chunks (default 200)
    """
    language_enum = LANGUAGE_MAP.get(language.lower())

    if language_enum:
        return RecursiveCharacterTextSplitter.from_language(
            language=language_enum,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
    
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )


def get_leftover_code(content: str, parsed_items: list[dict]) -> str:
    """Find lines NOT covered by any parsed function/class.

    WHAT ARE "LEFTOVERS"?
        After tree-sitter extracts all functions and classes, some lines remain:
        - import statements at the top
        - module-level constants (API_URL = "...")
        - global variables
        - if __name__ == "__main__": blocks

    WHY CHUNK THEM?
        These lines are still searchable code. If someone searches for
        "API configuration" and your constant is API_URL = "...", it should
        be findable. So we collect leftovers and text-split them.

    HOW:
        1. Mark all line numbers covered by functions/classes
        2. Collect all OTHER lines
        3. Join them into one string for text splitting
    """
    lines = content.splitlines()
    covered = set()

    # Mark lines that belong to parsed functions/classes
    for item in parsed_items:
        for line_num in range(item["start_line"], item["end_line"] + 1):
            covered.add(line_num)
    
    # Collect everything else
    leftover_lines = []
    for index, line in enumerate(lines, start=1):  # 1-indexed to match parser
        if index not in covered:
            leftover_lines.append(line)

    return "\n".join(leftover_lines).strip()


# =============================================================================
# chunk_file_with_parser() — Strategy 1: Tree-Sitter Based
# =============================================================================

def chunk_file_with_parser(file_info: dict, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[dict]:
    """Use tree-sitter to extract function/class level chunks.

    EXECUTION FLOW:
        1. Parse file with tree-sitter → list of {name, type, code, start_line, end_line}
           Example: [{"name": "login", "type": "function", "code": "def login()...", ...}]
        
        2. For each parsed item:
           - If code fits in chunk_size → ONE chunk with full metadata
           - If code is too big → text-split into smaller pieces (keep metadata)
        
        3. Collect leftover code (imports, constants) → text-split those too
        
        4. Return list of chunk dicts, each with:
           {"text", "file_path", "language", "chunk_index", "source",
            "file_hash", "symbol_name", "symbol_type", "start_line", "end_line"}

    WHY "source" FIELD?
        Tracks whether this chunk came from "parser" (tree-sitter) or
        "text_splitter" (fallback). Useful for debugging and quality metrics.
    """
    language = file_info["language"]
    file_path = file_info["absolute_path"]
    relative_path = file_info["file_path"]

    # Step 1: Parse with tree-sitter
    parsed_items = parse_file(file_path, language)
    if not parsed_items:
        return []  # Parser found nothing → caller will use text fallback

    chunks = []
    chunk_index = 0
    splitter = get_splitter(language, chunk_size, chunk_overlap)

    content = read_file_content(file_path)
    content_hash = file_hash(content)

    # Step 2: Each function/class → chunk(s)
    for item in parsed_items:
        code = item["code"]

        if len(code) <= chunk_size:
            # Small enough → one chunk, keep it whole
            chunks.append({
                "text": code,
                "file_path": relative_path,
                "language": language,
                "chunk_index": chunk_index,
                "source": "parser",
                "file_hash": content_hash,
                "symbol_name": item["name"],
                "symbol_type": item["type"],
                "start_line": item["start_line"],
                "end_line": item["end_line"],
            })
            chunk_index += 1
        else:
            # Too big (e.g., 200-line class) → split but keep metadata
            sub_texts = splitter.split_text(code)
            for sub_text in sub_texts:
                chunks.append({
                    "text": sub_text,
                    "file_path": relative_path,
                    "language": language,
                    "chunk_index": chunk_index,
                    "source": "parser",
                    "symbol_name": item["name"],
                    "symbol_type": item["type"],
                    "start_line": item["start_line"],
                    "end_line": item["end_line"],
                    "file_hash": content_hash,
                })
                chunk_index += 1

    # Step 3: Leftover top-level code
    leftover = get_leftover_code(content, parsed_items)

    if leftover.strip():
        leftover_texts = splitter.split_text(leftover)
        for text in leftover_texts:
            chunks.append({
                "text": text,
                "file_path": relative_path,
                "language": language,
                "chunk_index": chunk_index,
                "source": "text_splitter",  # These aren't from parser
                "symbol_name": None,
                "symbol_type": None,
                "start_line": None,
                "end_line": None,
                "file_hash": content_hash,
            })
            chunk_index += 1

    return chunks


# =============================================================================
# chunk_file() — Main Entry Point (picks strategy)
# =============================================================================

def chunk_file(file_info: dict, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[dict]:
    """Split a file into chunks. Uses tree-sitter when possible, text splitter otherwise.

    DECISION FLOW:
        1. Is the file empty? → return []
        2. Is the language supported by tree-sitter (in GRAMMAR_MAP)?
           YES → try chunk_file_with_parser()
                  If parser found functions → return those chunks
                  If parser found nothing → fall through to text splitting
           NO  → go directly to text splitting
        3. Text splitting fallback: split by character count with overlap

    FALLBACK CASES:
        - Language not in GRAMMAR_MAP (e.g., "yaml", "markdown")
        - Parser returns empty (file has no named functions — just top-level code)
        - Parser crashes (corrupt file, unsupported syntax)
    """
    content = read_file_content(file_info["absolute_path"])

    if not content.strip():
        return []
    
    language = file_info["language"]

    # Try parser-based chunking for supported languages
    if language in GRAMMAR_MAP:
        parser_chunks = chunk_file_with_parser(file_info, chunk_size, chunk_overlap)
        if parser_chunks:
            return parser_chunks
        
    # Fallback: pure text splitting (no symbol metadata)
    splitter = get_splitter(language, chunk_size, chunk_overlap)
    texts = splitter.split_text(content)

    return [
        {
            "text": text,
            "file_path": file_info["file_path"],
            "language": language,
            "chunk_index": index,
            "source": "text_splitter",
            "symbol_name": None,
            "symbol_type": None,
            "start_line": None,
            "end_line": None,
            "file_hash": file_hash(content),
        }
        for index, text in enumerate(texts)
    ]


# =============================================================================
# chunk_all_files() — Batch Processing
# =============================================================================

def chunk_all_files(files: list[dict], chunk_size: int = 1000, chunk_overlap: int = 200) -> list[dict]:
    """Chunk ALL scanned files. Returns flat list of all chunks across all files.

    Called by pipeline.py during full indexing.
    Processes files sequentially with progress logging every 100 files.

    For a 2000-file repo, typically produces 10,000-20,000 chunks.
    """
    all_chunks = []
    total = len(files)

    for i, file_info in enumerate(files, 1):
        chunks = chunk_file(file_info, chunk_size, chunk_overlap)
        all_chunks.extend(chunks)
        if i % 100 == 0 or i == total:
            _log(f"  Chunked {i}/{total} files ({len(all_chunks)} chunks so far)")

    return all_chunks

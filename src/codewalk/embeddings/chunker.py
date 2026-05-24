from pathlib import Path
import hashlib
import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter, Language

from src.codewalk.analysis.code_parser import parse_file, GRAMMAR_MAP
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

# Map our language names to LangChain's Language enum
LANGUAGE_MAP =  {
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

def file_hash(content: str) -> str:
    """Generate an MD5 hash of the file content for change detection."""
    return hashlib.md5(content.encode()).hexdigest()

def read_file_content(file_path: str) -> str:
    """Read a file's content. Return empty string if it can't be read."""
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return ""
    
def get_splitter(language: str, chunk_size: int = 1000, chunk_overlap: int = 200):
    """Get the right splitter for a language. Falls back to generic if unknown."""
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
    """
    Find lines NOT covered by any parsed function/class.
    These are top-level lines like imports, constants, global statements.
    We collect them into one string so the text splitter can chunk them.
    """

    lines = content.splitlines()
    covered = set()

    for item in parsed_items:
        for line_num in range(item["start_line"], item["end_line"] + 1):
            covered.add(line_num)
        
    leftover_lines = []
    for index, line in enumerate(lines, start=1):
        if index not in covered:
            leftover_lines.append(line)

    return "\n".join(leftover_lines).strip()

def chunk_file_with_parser(file_info: dict, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[dict]:
    """Use tree-sitter to extract function/class level chunks.

    Produces parent-child pairs:
      - PARENT: full function/class body → stored for LLM context
      - CHILDREN: sub-chunks of large functions → searched for precision
      - Small functions (fit in chunk_size) → parent only, no children
      - Leftover top-level code → text-split, type="leftover"
    """
    language = file_info["language"]
    file_path = file_info["absolute_path"]
    relative_path = file_info["file_path"]

    # Step 1: Parse the file with tree-sitter
    parsed_items = parse_file(file_path, language)
    if not parsed_items:
        return []

    chunks = []
    chunk_index = 0
    splitter = get_splitter(language, chunk_size, chunk_overlap)

    content = read_file_content(file_path)
    content_hash = file_hash(content)

    # Step 2: Each parsed function/class → parent + optional children
    for item in parsed_items:
        code = item["code"]

        parent_id = f"{relative_path}::parent::{chunk_index}"

        parent_chunk = {
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
            "chunk_type": "parent",             
            "parent_chunk_id": None,            
        }

        chunks.append(parent_chunk)
        chunk_index += 1

        if len(code) <= chunk_size:
            # Fits in one chunk — keep it whole
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
                "chunk_type": "child",              
                "parent_chunk_id": parent_id,       
            })
            chunk_index += 1
        else:
            # Too big — split it but keep metadata
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
                    "chunk_type": "child",      
                    "parent_chunk_id": parent_id,
                })
                chunk_index += 1

    # Step 3: Leftover top-level code (imports, constants, etc.)
    leftover = get_leftover_code(content, parsed_items)

    if leftover.strip():
        leftover_texts = splitter.split_text(leftover)
        for text in leftover_texts:
            chunks.append({
                "text": text,
                "file_path": relative_path,
                "language": language,
                "chunk_index": chunk_index,
                "source": "text_splitter",
                "symbol_name": None,
                "symbol_type": None,
                "start_line": None,
                "end_line": None,
                "file_hash": content_hash,
                "chunk_type": "leftover",
                "parent_chunk_id": None,
            })
            chunk_index += 1

    return chunks

def chunk_file(file_info: dict, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[dict]:
    """Split a file into chunks. Uses tree-sitter parser when possible,
    falls back to text splitter for unsupported languages."""
    content = read_file_content(file_info["absolute_path"])

    if not content.strip():
        return []
    
    language = file_info["language"]

    # Try parser first for supported languages
    if language in GRAMMAR_MAP:
        parser_chunks = chunk_file_with_parser(file_info, chunk_size, chunk_overlap)

        if parser_chunks:
            return parser_chunks
        
    # Fallback: pure text splitting (V0.1 behavior)
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
            "chunk_type": "leftover",
            "parent_chunk_id": None,
        }
        for index, text in enumerate(texts)
    ]

def chunk_all_files(files: list[dict], chunk_size: int = 1000, chunk_overlap: int = 200) -> list[dict]:
    """Chunk all scanned files. Returns flat list of all chunks."""
    all_chunks = []
    total = len(files)

    for i, file_info in enumerate(files, 1):
        chunks = chunk_file(file_info, chunk_size, chunk_overlap)
        all_chunks.extend(chunks)
        if i % 100 == 0 or i == total:
            _log(f"  Chunked {i}/{total} files ({len(all_chunks)} chunks so far)")
    
    return all_chunks
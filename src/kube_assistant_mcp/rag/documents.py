"""Deterministic Markdown chunks with provenance; oversized fenced blocks fail explicitly."""

import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    source: str
    heading: str
    text: str
    content_hash: str
    reviewed: str

    def payload(self):
        return asdict(self)


def load_chunks(root: Path, max_chars: int = 1600, overlap: int = 160) -> list[Chunk]:
    if not 200 <= max_chars <= 16000 or not 0 <= overlap < max_chars // 2:
        raise ValueError("Invalid chunk size/overlap")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Knowledge root must be a directory")
    chunks = []
    for path in sorted(root.rglob("*.md")):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Knowledge symlinks outside the root are not allowed")
        if path.stat().st_size > 1_000_000:
            raise ValueError("Document exceeds 1 MB limit")
        source = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        content_hash = sha256(text.encode()).hexdigest()
        reviewed_match = re.search(r"^Reviewed: (\d{4}-\d{2}-\d{2})$", text, re.MULTILINE)
        reviewed = reviewed_match[1] if reviewed_match else "unspecified"
        doc_id = str(uuid5(NAMESPACE_URL, source))
        heading = path.stem
        blocks, paragraph, fence = [], [], None

        def flush(heading, paragraph=paragraph, blocks=blocks):
            if paragraph:
                blocks.append((heading, "\n".join(paragraph).strip()))
                paragraph.clear()

        for line in text.splitlines():
            marker = re.match(r"^\s*(`{3,}|~{3,})", line)
            if fence:
                paragraph.append(line)
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                    fence = None
                    flush(heading)
            elif marker:
                flush(heading)
                fence = marker[1]
                paragraph.append(line)
            elif line.startswith("#") and re.match(r"^#{1,6} ", line):
                flush(heading)
                heading = line.lstrip("#").strip()
            elif not line.strip():
                flush(heading)
            else:
                paragraph.append(line)
        if fence:
            raise ValueError(f"Unclosed code fence in {source}")
        flush(heading)
        grouped = []
        current, current_heading = "", ""
        for block_heading, block in blocks:
            if len(block) > max_chars and re.match(r"^(`{3,}|~{3,})", block):
                raise ValueError(f"Code block exceeds chunk limit in {source}")
            pieces = (
                [block[i : i + max_chars] for i in range(0, len(block), max_chars - overlap)]
                if len(block) > max_chars
                else [block]
            )
            for piece in pieces:
                if current and (
                    block_heading != current_heading or len(current) + len(piece) + 2 > max_chars
                ):
                    grouped.append((current_heading, current))
                    # Overlap only plain prose within a section; never split code fences.
                    tail = (
                        current[-overlap:]
                        if overlap and "```" not in current and "~~~" not in current
                        else ""
                    )
                    current = (
                        tail
                        if block_heading == current_heading and len(tail) + len(piece) + 2 <= max_chars
                        else ""
                    )
                current_heading = block_heading
                current = (current + "\n\n" + piece).strip()
        if current:
            grouped.append((current_heading, current))
        for index, (heading, passage) in enumerate(grouped):
            identity = f"{source}:{content_hash}:{max_chars}:{overlap}:{index}"
            chunks.append(
                Chunk(
                    str(uuid5(NAMESPACE_URL, identity)),
                    doc_id,
                    source,
                    heading,
                    passage,
                    content_hash,
                    reviewed,
                )
            )
    if not chunks:
        raise ValueError("Knowledge directory contains no nonempty Markdown documents")
    if len(chunks) > 10000:
        raise ValueError("Knowledge corpus exceeds 10000 chunks")
    return chunks

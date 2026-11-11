"""Fixed-size chunker for pipeline 1.0: a recursive splitter measured in whitespace tokens.

The semantics of the common "recursive character splitter", in tokens:

1. Split each page on the first separator present in its text (``\\n\\n``, then ``\\n``, then a
   space), always, even when the page would fit. A piece still larger than ``size`` is split again with
   the next separators; a piece with no separator left is cut every ``size`` words.
2. Merge the pieces of all pages, in order, into chunks of at most ``size`` tokens.
3. Start each next chunk with the trailing ``overlap`` tokens of the previous one: whole trailing pieces
   first, topped up with the last words of the piece before them, so chunks really overlap by
   ``overlap`` tokens (the naive lane: 300 tokens, 20 %) even when the pieces are long.

Chunks know which pages their tokens came from; they carry no bbox, breadcrumb or ontology.
"""

from __future__ import annotations

from dataclasses import dataclass

SEPARATORS = ("\n\n", "\n", " ")


@dataclass
class Piece:
    text: str
    page: int

    @property
    def tokens(self) -> int:
        return len(self.text.split())


@dataclass
class Chunk:
    text: str
    pages: list[int]
    tokens: int


def _split(text: str, page: int, size: int, seps: tuple[str, ...]) -> list[Piece]:
    sep_index = next((i for i, sep in enumerate(seps) if sep in text), None)
    if sep_index is None:
        words = text.split()
        return [Piece(" ".join(words[i:i + size]), page) for i in range(0, len(words), size)]
    sep, rest = seps[sep_index], seps[sep_index + 1:]
    out: list[Piece] = []
    for part in text.split(sep):
        part = part.strip()
        if not part:
            continue
        piece = Piece(part, page)
        out.extend([piece] if piece.tokens <= size else _split(part, page, size, rest))
    return out


def _tail(window: list[Piece], overlap: int) -> list[Piece]:
    """The trailing ``overlap`` tokens of ``window`` as pieces (the last one may be a word suffix)."""
    carried: list[Piece] = []
    tokens = 0
    for prev in reversed(window):
        if tokens >= overlap:
            break
        need = overlap - tokens
        if prev.tokens <= need:
            carried.insert(0, prev)
            tokens += prev.tokens
        else:
            carried.insert(0, Piece(" ".join(prev.text.split()[-need:]), prev.page))
            tokens += need
    return carried


def _trim_front(pieces: list[Piece], limit: int) -> list[Piece]:
    """Drop tokens from the front until at most ``limit`` remain."""
    out = list(pieces)
    while out and sum(p.tokens for p in out) > limit:
        excess = sum(p.tokens for p in out) - limit
        head = out[0]
        if head.tokens <= excess:
            out.pop(0)
        else:
            out[0] = Piece(" ".join(head.text.split()[excess:]), head.page)
    return out


def split_pages(pages: list[tuple[int, str]], *, size: int, overlap: int) -> list[Chunk]:
    """``pages`` = [(page number, text)] in order; empty pages produce nothing."""
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError(f"invalid chunk size/overlap {size}/{overlap}")
    pieces: list[Piece] = []
    for page, text in pages:
        if text.strip():
            pieces.extend(_split(text.strip(), page, size, SEPARATORS))
    chunks: list[Chunk] = []
    window: list[Piece] = []
    tokens = 0
    fresh = 0  # tokens in the window that no earlier chunk emitted
    for piece in pieces:
        if fresh and tokens + piece.tokens > size:
            chunks.append(_emit(window))
            window = _trim_front(_tail(window, overlap), size - piece.tokens)
            tokens, fresh = sum(p.tokens for p in window), 0
        window.append(piece)
        tokens += piece.tokens
        fresh += piece.tokens
    if fresh:
        chunks.append(_emit(window))
    return chunks


def _emit(window: list[Piece]) -> Chunk:
    text = "\n".join(p.text for p in window)
    return Chunk(text=text, pages=sorted({p.page for p in window}), tokens=len(text.split()))

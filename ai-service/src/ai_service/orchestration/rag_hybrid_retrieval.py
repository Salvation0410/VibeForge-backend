"""固定语料对照使用的纯排序算法，不读取配置、事实标签或外部资源。"""

from collections import Counter
import math
import re
import unicodedata


# 汉字与 ASCII 分开匹配，标点、空白和其他字符会切断双字词元。
_HAN = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\U00030000-\U000323af"
_SEGMENTS = re.compile(rf"[{_HAN}]+|[a-z0-9_]+")
_ASCII_WORD = re.compile(r"[a-z0-9_]+")


def tokenize(text: str) -> list[str]:
    """NFKC 归一化后生成中文相邻双字及小写 ASCII 词元。"""
    if not isinstance(text, str):
        raise ValueError("text 必须是字符串")
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    for match in _SEGMENTS.finditer(normalized):
        segment = match.group()
        if _ASCII_WORD.fullmatch(segment) or len(segment) == 1:
            tokens.append(segment)
        else:
            tokens.extend(segment[index:index + 2] for index in range(len(segment) - 1))
    return tokens


def _validate_limit(limit: int) -> None:
    if type(limit) is not int or limit < 0:
        raise ValueError("limit 必须是非负整数")


def bm25_top(chunks: list[dict], question: str, limit: int = 8) -> list[tuple[int, float]]:
    """按固定 BM25 参数返回正分候选，同分时使用原语料序号。"""
    _validate_limit(limit)
    if not isinstance(chunks, list):
        raise ValueError("chunks 必须是列表")
    # 保留查询词首次出现顺序，避免集合迭代顺序影响浮点累加。
    query_tokens = list(dict.fromkeys(tokenize(question)))
    documents: list[Counter[str]] = []
    lengths: list[int] = []
    for chunk in chunks:
        if not isinstance(chunk, dict) or not isinstance(chunk.get("content"), str):
            raise ValueError("每个 chunk 必须包含字符串 content")
        tokens = tokenize(chunk["content"])
        documents.append(Counter(tokens))
        lengths.append(len(tokens))
    if not documents or not query_tokens or not any(lengths) or limit == 0:
        return []
    count = len(documents)
    # 空文档计入 N 和平均长度；既不伪造词元，也不产生正分命中。
    average_length = sum(lengths) / count
    frequencies = Counter(term for document in documents for term in document)
    idf = {
        term: math.log(1 + (count - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
        for term in query_tokens
    }
    scores: list[tuple[int, float]] = []
    for index, document in enumerate(documents):
        normalization = 1.2 * (0.25 + 0.75 * lengths[index] / average_length)
        score = sum(
            idf[term] * document[term] * 2.2 / (document[term] + normalization)
            for term in query_tokens if document[term] > 0
        )
        if score > 0:
            scores.append((index, score))
    return sorted(scores, key=lambda item: (-item[1], item[0]))[:limit]


def rrf_top(
    dense_indices: list[int], sparse_indices: list[int], limit: int = 8
) -> list[tuple[int, float]]:
    """按等权 k=60 的 RRF 融合；单路内重复拒绝，跨路重叠累加。"""
    _validate_limit(limit)
    scores: dict[int, float] = {}
    for indices in (dense_indices, sparse_indices):
        if not isinstance(indices, list):
            raise ValueError("排名必须是索引列表")
        seen: set[int] = set()
        for rank, index in enumerate(indices, start=1):
            # 未传入语料大小，仅验证非负整数；上界由调用方的语料绑定核对。
            if type(index) is not int or index < 0 or index in seen:
                raise ValueError("每路排名必须包含互不重复的非负整数索引")
            seen.add(index)
            scores[index] = scores.get(index, 0.0) + 1 / (60 + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]

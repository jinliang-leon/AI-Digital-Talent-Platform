"""Deterministic retrieval primitives shared by talent search and the knowledge base."""
from collections import Counter, defaultdict
from math import log

from .embedding import lexical_vector, terms


def rank(query, rows, vector_for, result_limit=10):
    """Return document rows with per-route rank and reranking diagnostics."""
    if not rows:
        return []
    tokens = Counter(terms(query))
    if not tokens:
        return []
    corpus = [(row, Counter(terms(row.text))) for row in rows]
    counts = Counter(token for _, words in corpus for token in tokens if token in words)
    averages = sum(sum(words.values()) for _, words in corpus) / len(corpus) or 1
    lexical, semantic, matched = {}, {}, {}
    sparse_query = {key: [(index, value) for index, value in enumerate(vector) if value]
                    for key, vector in vector_for.items()}
    for row, words in corpus:
        length = sum(words.values())
        weighted = 0.0
        for token in tokens:
            frequency = words[token]
            if frequency:
                idf = log(1 + (len(corpus) - counts[token] + .5) / (counts[token] + .5))
                weighted += idf * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * length / averages))
        vector = vector_for.get(row.embedding_revision)
        embedding = row.embedding
        if weighted and not embedding and row.embedding_revision == "lexical-hash-256-v1":
            embedding = lexical_vector(row.text)
        comparable = vector and len(vector) == len(embedding)
        candidate = weighted or row.embedding_revision != "lexical-hash-256-v1"
        cosine = (sum(value * embedding[index] for index, value in sparse_query[row.embedding_revision])
                  if candidate and comparable else 0)
        if weighted:
            lexical[row.key] = weighted
        if cosine > 0:
            semantic[row.key] = cosine
        matched[row.key] = sum(token in words for token in tokens) / len(tokens)

    scores = defaultdict(float)
    routes = defaultdict(list)
    for route, values in (("keyword", lexical), ("vector", semantic)):
        order = sorted(values, key=lambda key: (-values[key], key))[:100]
        for position, key in enumerate(order, 1):
            scores[key] += 1 / (60 + position)
            routes[key].append(route)
    by_key = {row.key: row for row in rows}
    shortlist = sorted(scores, key=lambda key: (-scores[key], key))[:30]
    reranked = sorted(shortlist, key=lambda key: (
        -(scores[key] + 0.012 * matched[key] + 0.004 * max(0, semantic.get(key, 0))), key
    ))
    return [(by_key[key], routes[key]) for key in reranked[:result_limit]]

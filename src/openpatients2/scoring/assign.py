"""One-to-one maximum-weight assignment (Hungarian) over sparse eligible pairs."""
from __future__ import annotations


def _hungarian_max(weights):
    """Maximum-weight assignment on a dense n x m matrix (0 = no pair). Returns [(i, j)] with weight > 0."""
    n, m = len(weights), len(weights[0]) if weights else 0
    size = max(n, m)
    if not size:
        return []
    big = max((w for row in weights for w in row), default=0.0)
    cost = [[(big - weights[i][j]) if i < n and j < m else big for j in range(size)] for i in range(size)]
    INF = float('inf')
    u = [0.0] * (size + 1); v = [0.0] * (size + 1); p = [0] * (size + 1); way = [0] * (size + 1)
    for i in range(1, size + 1):
        p[0] = i; j0 = 0
        minv = [INF] * (size + 1); used = [False] * (size + 1)
        while True:
            used[j0] = True; i0 = p[j0]; delta = INF; j1 = 0
            row = cost[i0 - 1]
            for j in range(1, size + 1):
                if not used[j]:
                    cur = row[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur; way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]; j1 = j
            for j in range(size + 1):
                if used[j]:
                    u[p[j]] += delta; v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]; p[j0] = p[j1]; j0 = j1
    out = []
    for j in range(1, size + 1):
        i = p[j] - 1; jj = j - 1
        if i < n and jj < m and weights[i][jj] > 0:
            out.append((i, jj))
    return out


def assign(pairs):
    """pairs: {(i, j): weight>0}. Returns a one-to-one list [(i, j)] maximising total weight.

    Decomposes into connected components so the cubic step only runs on small blocks.
    """
    if not pairs:
        return []
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    for i, j in pairs:
        a, b = find(('r', i)), find(('c', j))
        if a != b:
            parent[a] = b
    comps = {}
    for (i, j), w in pairs.items():
        comps.setdefault(find(('r', i)), []).append((i, j, w))
    out = []
    for edges in comps.values():
        if len(edges) == 1:
            out.append(edges[0][:2]); continue
        rows = sorted({e[0] for e in edges}); cols = sorted({e[1] for e in edges})
        ri = {r: k for k, r in enumerate(rows)}; ci = {c: k for k, c in enumerate(cols)}
        mat = [[0.0] * len(cols) for _ in rows]
        for i, j, w in edges:
            mat[ri[i]][ci[j]] = w
        out.extend((rows[a], cols[b]) for a, b in _hungarian_max(mat))
    return sorted(out)

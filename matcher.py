from batch_store import MatchAssignment, MatchRecord, PhotoItem

AGE_DIFF_WARNING_THRESHOLD = 10
_UNKNOWN_AGE_COST = 10_000_000
_INF = 10**18


def _hungarian_min_cost(cost_matrix: list[list[int]]) -> list[tuple[int, int]]:
    """
    Решает задачу о назначениях минимальной стоимости для прямоугольной матрицы R x C.
    Возвращает ровно min(R, C) пар (row_idx, col_idx).
    """
    rows = len(cost_matrix)
    if rows == 0:
        return []
    cols = len(cost_matrix[0])
    if cols == 0:
        return []

    transposed = False
    matrix = cost_matrix
    if rows > cols:
        transposed = True
        matrix = [[cost_matrix[r][c] for r in range(rows)] for c in range(cols)]
        rows, cols = cols, rows

    # Теперь rows <= cols; 1-индексация для классического алгоритма Куна-Манкреса
    u = [0] * (rows + 1)
    v = [0] * (cols + 1)
    p = [0] * (cols + 1)
    way = [0] * (cols + 1)

    for i in range(1, rows + 1):
        p[0] = i
        j0 = 0
        minv = [_INF] * (cols + 1)
        used = [False] * (cols + 1)

        while True:
            used[j0] = True
            i0 = p[j0]
            delta = _INF
            j1 = 0
            for j in range(1, cols + 1):
                if not used[j]:
                    cur = matrix[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(cols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break

        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    pairs: list[tuple[int, int]] = []
    for j in range(1, cols + 1):
        if p[j] != 0:
            r, c = p[j] - 1, j - 1
            if transposed:
                pairs.append((c, r))
            else:
                pairs.append((r, c))

    pairs.sort(key=lambda pair: pair[0])
    return pairs


def _pair_cost(record: MatchRecord, photo: PhotoItem) -> int:
    if record.age is not None and photo.age is not None:
        diff = abs(record.age - photo.age)
        return diff * 1000 + diff * diff
    return _UNKNOWN_AGE_COST


def build_assignment(
    record: MatchRecord,
    photo: PhotoItem,
    mode: str = "auto",
) -> MatchAssignment:
    if record.age is not None and photo.age is not None:
        diff: int | None = abs(record.age - photo.age)
        if diff > AGE_DIFF_WARNING_THRESHOLD:
            warning = (
                f"⚠️ Большая разница возраста: в строке {record.age} л., "
                f"на фото ~{photo.age} л. (Δ{diff})"
            )
        else:
            warning = ""
    else:
        diff = None
        warning = "⚠️ Возраст не определён — проверьте фото вручную"

    return MatchAssignment(
        record_index=record.index,
        photo_index=photo.index,
        mode=mode,  # type: ignore[arg-type]
        age_diff=diff,
        warning=warning,
    )


def match_batch(
    records: list[MatchRecord],
    photos: list[PhotoItem],
) -> dict[int, MatchAssignment]:
    """
    Оптимальное 1-к-1 сопоставление внутри каждого пола (M↔M, F↔F).
    Записи и фото с полом 'U' в автоподборе не участвуют.
    """
    assignments: dict[int, MatchAssignment] = {}

    for gender in ("M", "F"):
        g_records = [r for r in records if r.gender == gender]
        g_photos = [p for p in photos if p.gender == gender]
        if not g_records or not g_photos:
            continue

        cost_matrix = [
            [_pair_cost(rec, ph) for ph in g_photos]
            for rec in g_records
        ]
        pairs = _hungarian_min_cost(cost_matrix)
        for r_pos, p_pos in pairs:
            rec = g_records[r_pos]
            ph = g_photos[p_pos]
            assignments[rec.index] = build_assignment(rec, ph, mode="auto")

    return assignments

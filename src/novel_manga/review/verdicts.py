"""A saved video address is not a completed model verdict."""


def invalid_verdict_ids(rows: dict) -> list[str]:
    return sorted(cid for cid,row in rows.items() if row.get('severity') not in {'pass','minor','fail'})

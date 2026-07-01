import re


FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_page_range(raw):
    return (
        (raw or "")
        .translate(FULLWIDTH_DIGITS)
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("~", "-")
        .replace("～", "-")
    )


def parse_page_range(raw, total_pages):
    raw = normalize_page_range(raw).strip()
    if not raw:
        return list(range(1, total_pages + 1)), ""
    pages = set()
    errors = []
    for token in re.split(r"[,，、;；\s]+", raw):
        token = token.strip()
        if not token:
            continue
        if re.fullmatch(r"\d+", token):
            start = end = int(token)
        elif re.fullmatch(r"\d+\s*-\s*\d+", token):
            left, right = re.split(r"\s*-\s*", token)
            start, end = int(left), int(right)
            if start > end:
                start, end = end, start
        else:
            errors.append(token)
            continue
        for page in range(start, end + 1):
            if 1 <= page <= total_pages:
                pages.add(page)
    if errors:
        raise ValueError("页码范围格式错误：" + "、".join(errors))
    if not pages:
        raise ValueError("页码范围没有匹配到有效页面")
    return sorted(pages), raw

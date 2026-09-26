"""Public editorial identity; no invented credentials or investment promises."""

BRAND_NAME = "서울 아파트 실거래 데이터랩"
NICKNAME = "서울 실거래 데이터랩"
AUTHOR_LABEL = f"{BRAND_NAME} (개인 연구)"
DESCRIPTION = (
    "국토교통부 공개 실거래를 바탕으로 서울 아파트 가격대·전용면적·거래 표본을 정리합니다. "
    "자료 기준일과 출처를 함께 공개하며, 호가·투자 추천·미래 가격 예측은 제공하지 않습니다."
)


def creator_schema() -> dict[str, str]:
    """The visible author and structured-data creator share one truthful label."""
    return {"@type": "Organization", "name": AUTHOR_LABEL}

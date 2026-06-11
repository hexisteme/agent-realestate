"""agent_realestate — 한국 부동산 매수 의사결정 리포트 시스템.

agent_money 와 동일한 설계 철학:
  - 가드레일을 타입으로 강제 (domain.py): 모든 줄에 Provenance(FACT/INFERENCE/ASSUMPTION),
    매물(Listing)은 4요소 없으면 생성 불가, 추정 호가는 ASKING_LIVE 로 승격 불가.
  - 3계층 하이브리드: Static(정책·재건축 캐시, cron) / Live(네이버 호가, Claude-MCP 주입) /
    Compute(LTV·DSR·세금·수익률·점수, 결정론 standalone).
  - 작명은 ~/.claude/rules/glossary-real-estate.md 를 따른다.
"""

__version__ = "0.1.0"

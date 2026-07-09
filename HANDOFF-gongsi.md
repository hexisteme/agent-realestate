# HANDOFF — 공시가(gongsi_man) 백필 파이프라인 구축 ✅ 완료 (2026-07-09)

> **완료**: `collect_gongsi.py` 로 94/117 백필, 알리미 3표본(구로·강서·노원) 호 단위 대조 일치,
> 공시가율 분포 중위 0.61(0.36~0.71), 사이트 push 0fa80d7. 잔여 8건은 보수적 차단
> (필지 불일치 1·공시가율 범위밖 3·평형 무매칭 3·무자료 1) — 아래는 이력 기록용.

## 선행조건 (사용자 액션)

- [ ] vworld.kr 회원가입 → [오픈API]→[인증키 발급] (서비스 URL `http://localhost`) → 즉시 발급
- [ ] 발급 키를 `/Volumes/EXT_SSD/bot/agent_realestate/.env` 에 `VWORLD_API_KEY=<키>` 추가 (chmod 600 유지)

## 현재 상태 (2026-07-08 완료분 — 재작업 금지)

- 블로그 7필드 중 6필드 백필 완료: 난방·복도·주차·시공사(K-apt V4)·인근초등(KAKAO SC4)·관리비(15057937 V2).
  **gongsi_man 만 잔여** (발행 dataset 0/117, run_daily 가 `ℹ️ [coverage-pending] gongsi_man` 출력 중).
- universe = `examples/candidates_universe159_20260707.json` (run_daily `_latest_or` glob 자동 픽업).
  `kapt_verified=True` 123건에 `kapt_code` 확정(세대수·준공 교차검증 통과분).
- 구 `agent_realestate/collectors/gongsi.py` 의 ApartHousingPriceService3 엔드포인트는 **서비스 폐기 확정**
  (전 버전 HTTP500) — docstring 에 기록됨. 현행 공시가 개방 = data.go.kr 15124003 → **VWorld 제공**.

## 작업 절차

1. **프리플라이트**: `.env` 에 VWORLD_API_KEY 존재 확인 (없으면 사용자에게 위 선행조건 안내 후 중단).
2. **엔드포인트 라이브 검증** (아직 미검증 — 가설): NSDI 데이터 API
   `https://api.vworld.kr/ned/data/getApartHousingPriceAttr?key=<VWORLD_API_KEY>&pnu=<19자리>&stdrYear=2026&format=json&numOfRows=100`
   - 테스트 pnu: 신도림태영타운 — basis V4(kaptCode A15205513)의 `bjdCode=1153010200` + 지번(kaptAddr
     "구로동 1267") → pnu = `1153010200` + `1`(대지) + `1267`.zfill(4) + `0000` = `1153010200112670000`.
   - 응답에 호별 privArea·공시가격(pblntfcPc 류) 필드가 오는지 확인. 안 오면 VWorld API 목록에서
     '공동주택가격 속성조회' 정확명 재검색 (vworld.kr 오픈API 문서).
3. **pnu 매핑**: 별도 지오코딩 불필요 — `fetch_basis_v4()` 응답의 `bjdCode`(10자리) + `kaptAddr` 지번
   파싱(본번-부번)으로 조립. 산번지("산123")는 필지구분 '2'. 부번 없으면 0000.
4. **배치 작성** `collect_gongsi.py` (repo 루트, gitignored dev tool — collect_kapt_maint_fees.py 패턴):
   - 대상: `kapt_verified=True` 단지만 (첫-매치 오매칭 교훈 — HANDOFF 하단 '지뢰' 참조).
   - 단지 pnu → VWorld 공시가 조회 → **동일평형 매칭**(레코드 privArea vs universe `area_exclusive_m2`
     ±3.5㎡) → 해당 호들의 최신 기준연도 공시가 **중위값** → `gongsi_man`(만원 단위 — 응답 단위가
     원인지 만원인지 반드시 실측 확인) → universe in-place 갱신.
   - 타당성 가드: gongsi_man 이 해당 단지 molit 실거래 중위의 20%~90% 범위 밖이면 None(공시가율 통상
     40~70%). 쿼터: VWorld 기본 일 4만콜 내외 — 123단지면 여유.
5. **재생성·발행**: `python3 -m blog.run_daily --asof <오늘> --today <오늘>` → dataset 커버리지 확인
   (`gongsi_man` n/117 출력) → `python3 -m blog.build_site` → site repo add/commit/push.
   당일 라이브 포스트 반영 원하면: 포스트 id 확인 후
   `TISTORY_NEWPOST_URL=https://floker.tistory.com/manage/newpost/<id> python3 blog/tistory_publish_pw.py --mode publish --date <오늘>`.
6. **검증 게이트 (load-bearing — 실명 사실 발행)**:
   - 표본 2~3단지의 산출 공시가를 부동산공시가격알리미(www.realtyprice.kr) 값과 수동 대조.
   - 적대 리뷰(area 매칭 오프바이·단위 혼동·타 단지 pnu 오조립) 1회 후 발행.

## 지뢰 (이번 주 실측 교훈 — 반복 금지)

- **이름 substring 첫-매치 금지**: 타 단지 사실이 실명 발행되는 critical (07-07, 5건 실측).
  kapt_verified 된 kapt_code/bjdCode 만 신뢰.
- **data.go.kr/VWorld 구버전 엔드포인트**: 죽은 서비스는 bare HTTP500 'Unexpected errors' — 미승인(403)과
  구분할 것.
- **티스토리 표는 카카오 paste 정규화 제약** (`blog/tistory_draft.py` docstring) — 공시가는 팩트라인에
  `공시가 X만` 으로 이미 배선돼 있어 draft 수정 불필요.
- 시크릿 원문을 대화/로그에 출력 금지. 커밋은 사용자가 요청할 때만.

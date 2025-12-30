import streamlit as st
import pandas as pd
from datetime import datetime
import hashlib

import gspread
from google.oauth2.service_account import Credentials


# =========================
# 기본 설정 / Secrets
# =========================
st.set_page_config(page_title="MG-ADL 설문", page_icon="🧠", layout="centered")

APP_PASSWORD = st.secrets.get("APP_PASSWORD", "0712")
SHEET_ID = st.secrets.get("SHEET_ID", "")
WORKSHEET_NAME = st.secrets.get("WORKSHEET_NAME", "responses")
SALT = st.secrets.get("SALT", "")

SA_INFO = st.secrets.get("GOOGLE_SERVICE_ACCOUNT", None)


# =========================
# MG-ADL 문항(0~3)
# =========================
ITEMS = [
    {"id": "mgadl_01_talking", "question": "말하기", "choices": {
        0: "정상",
        1: "때때로 불분명하거나 콧소리 나는 발음",
        2: "불분명하거나 콧소리가 나는 발음이 지속되나 이해할 수 있음",
        3: "말을 이해하기 어려움",
    }},
    {"id": "mgadl_02_chewing", "question": "씹기", "choices": {
        0: "정상",
        1: "고형 음식을 씹기가 어려움",
        2: "부드러운 음식을 씹기가 어려움",
        3: "위장 영양관",
    }},
    {"id": "mgadl_03_swallowing", "question": "삼키기", "choices": {
        0: "정상",
        1: "드물게 사래 들리는 경우가 있음",
        2: "자주 사래 들려 식사에 변화를 줄 필요가 있음",
        3: "위장 영양관",
    }},
    {"id": "mgadl_04_breathing", "question": "숨쉬기", "choices": {
        0: "정상",
        1: "힘든 활동 시 숨가쁨",
        2: "휴식 시 숨가쁨",
        3: "인공호흡기의존",
    }},
    {"id": "mgadl_05_brush_teeth_hair", "question": "양치나 머리를 빗을 때", "choices": {
        0: "어려움 없음",
        1: "힘이 더 들지만 쉬는 기간이 필요하지 않음",
        2: "쉬는 기간이 필요함",
        3: "이 기능 중 한 가지를 할 수 없음",
    }},
    {"id": "mgadl_06_arise_from_chair", "question": "의자에서 일어설 때", "choices": {
        0: "어려움 없음",
        1: "경증으로, 가끔 팔을 사용함",
        2: "중등도로, 항상 팔을 사용함",
        3: "중증으로, 도움이 필요함",
    }},
    {"id": "mgadl_07_diplopia", "question": "겹쳐보임(복시)", "choices": {
        0: "없음",
        1: "발생하나 매일 발생하지는 않음",
        2: "매일 발생하나 지속적이지는 않음",
        3: "지속적임",
    }},
    {"id": "mgadl_08_ptosis", "question": "눈꺼풀처짐(안검하수)", "choices": {
        0: "없음",
        1: "발생하나 매일 발생하지는 않음",
        2: "매일 발생하나 지속적이지는 않음",
        3: "지속적임",
    }},
]

# "우리 앱이 기대하는 헤더"
EXPECTED_HEADER = (
    ["created_at", "name", "dob", "patient_hash", "total_score"]
    + [it["id"] for it in ITEMS]
)


# =========================
# 유틸
# =========================
def compute_total(responses: dict) -> int:
    return int(sum(int(v) for v in responses.values()))


def patient_hash(name: str, dob: str) -> str:
    raw = f"{name}|{dob}|{SALT}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


@st.cache_resource(show_spinner=False)
def _get_gspread_client():
    if SA_INFO is None:
        raise RuntimeError("Secrets에 GOOGLE_SERVICE_ACCOUNT가 없습니다.")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(SA_INFO, scopes=scopes)
    return gspread.authorize(creds)


def get_worksheet():
    if not SHEET_ID:
        raise RuntimeError("Secrets에 SHEET_ID가 없습니다.")
    gc = _get_gspread_client()
    sh = gc.open_by_key(SHEET_ID)

    # 탭 없으면 생성
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=2000, cols=80)
    return ws


def ensure_header(ws):
    """
    - 시트 비어있으면 EXPECTED_HEADER로 생성
    - 기존 헤더가 있고, EXPECTED_HEADER에 있는 컬럼이 빠져있으면 자동으로 뒤에 추가
    - 이후 append는 "현재 헤더(1행)" 기준으로 record.get()로 안전 매핑
    """
    values = ws.get_all_values()
    if len(values) == 0:
        ws.append_row(EXPECTED_HEADER, value_input_option="USER_ENTERED")
        return EXPECTED_HEADER

    current = ws.row_values(1)
    # current가 비어있는 경우(가끔)
    if not current:
        ws.update("1:1", [EXPECTED_HEADER])
        return EXPECTED_HEADER

    missing = [h for h in EXPECTED_HEADER if h not in current]
    if missing:
        new_header = current + missing
        ws.update("1:1", [new_header])
        return new_header

    return current


def append_record_to_sheet(record: dict):
    """
    record는 최소한 EXPECTED_HEADER의 key를 갖는 dict.
    실제 append는 "현재 시트의 헤더"에 맞춰 column-safe하게 수행.
    """
    ws = get_worksheet()
    current_header = ensure_header(ws)

    row = [record.get(h, "") for h in current_header]
    res = ws.append_row(row, value_input_option="USER_ENTERED")

    # 디버그/확인용 반환
    updated_range = None
    if isinstance(res, dict):
        updated_range = res.get("updates", {}).get("updatedRange")

    return {
        "spreadsheet_title": ws.spreadsheet.title,
        "worksheet_title": ws.title,
        "updated_range": updated_range,
        "header_len": len(current_header),
    }


# =========================
# 세션 상태
# =========================
if "authed" not in st.session_state:
    st.session_state.authed = False

if "patient" not in st.session_state:
    st.session_state.patient = {"name": "", "dob": ""}

if "responses" not in st.session_state:
    st.session_state.responses = {}

if "saved" not in st.session_state:
    st.session_state.saved = False


def reset_all():
    st.session_state.authed = False
    st.session_state.patient = {"name": "", "dob": ""}
    st.session_state.responses = {}
    st.session_state.saved = False


# =========================
# UI
# =========================
st.title("🧠 MG-ADL 설문")
st.caption("1) 비밀번호/정보 → 2) 설문 → 3) 결과/저장 (Google Sheets 누적 저장)")

with st.sidebar:
    st.subheader("메뉴")
    page = st.radio("이동", ["1) 이름/생년월일", "2) 설문", "3) 결과/저장"], index=0)
    st.divider()
    st.write("접속 상태:", "✅ 인증됨" if st.session_state.authed else "⛔ 미인증")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("로그아웃"):
            st.session_state.authed = False
            st.rerun()
    with c2:
        if st.button("전체 초기화"):
            reset_all()
            st.rerun()

# 인증 안됐으면 2/3 차단
if not st.session_state.authed and page != "1) 이름/생년월일":
    st.warning("먼저 1) 페이지에서 비밀번호 인증을 완료해주세요.")
    st.stop()


# =========================
# 페이지 1
# =========================
if page == "1) 이름/생년월일":
    st.header("1) 대상자 정보 입력")

    if not st.session_state.authed:
        st.info("접속 비밀번호(0712)를 입력해야 다음 단계로 진행할 수 있습니다.")
        with st.form("auth_form"):
            pw = st.text_input("접속 비밀번호", type="password", placeholder="0712")
            ok = st.form_submit_button("인증")
        if ok:
            if pw == APP_PASSWORD:
                st.session_state.authed = True
                st.success("인증 완료!")
                st.rerun()
            else:
                st.error("비밀번호가 올바르지 않습니다.")
        st.stop()

    with st.form("patient_form", clear_on_submit=False):
        name = st.text_input("이름", value=st.session_state.patient["name"], placeholder="예: 홍길동")
        dob = st.date_input("생년월일", value=None)
        submitted = st.form_submit_button("저장")

    if submitted:
        if not name.strip():
            st.error("이름을 입력해주세요.")
        elif dob is None:
            st.error("생년월일을 선택해주세요.")
        else:
            st.session_state.patient["name"] = name.strip()
            st.session_state.patient["dob"] = dob.isoformat()
            st.session_state.saved = False
            st.success("저장되었습니다. 사이드바에서 '2) 설문'으로 이동하세요.")

    if st.session_state.patient["name"] and st.session_state.patient["dob"]:
        st.info(f"현재 입력값 → 이름: {st.session_state.patient['name']} / 생년월일: {st.session_state.patient['dob']}")


# =========================
# 페이지 2
# =========================
elif page == "2) 설문":
    st.header("2) MG-ADL 설문")

    if not (st.session_state.patient["name"] and st.session_state.patient["dob"]):
        st.warning("먼저 1) 페이지에서 이름/생년월일을 입력해주세요.")
        st.stop()

    st.write(f"대상자: **{st.session_state.patient['name']}** (DOB: {st.session_state.patient['dob']})")

    with st.form("survey_form"):
        new_responses = {}
        for item in ITEMS:
            options = list(item["choices"].keys())
            labels = [f"{k}점 - {item['choices'][k]}" for k in options]

            prev = st.session_state.responses.get(item["id"], 0)
            idx = options.index(int(prev)) if int(prev) in options else 0

            selected = st.radio(
                f"**{item['question']}**",
                options=labels,
                index=idx,
                key=f"radio_{item['id']}",
            )
            score = int(selected.split("점")[0].strip())
            new_responses[item["id"]] = score

        submitted = st.form_submit_button("응답 저장")

    if submitted:
        st.session_state.responses = new_responses
        st.session_state.saved = False
        total = compute_total(new_responses)
        st.success(f"응답 저장 완료! 현재 총점: **{total} / 24**")
        st.info("사이드바에서 '3) 결과/저장'으로 이동하세요.")


# =========================
# 페이지 3
# =========================
else:
    st.header("3) 결과/저장")

    if not st.session_state.responses:
        st.warning("먼저 2) 설문을 완료해주세요.")
        st.stop()

    name = st.session_state.patient["name"]
    dob = st.session_state.patient["dob"]
    ph = patient_hash(name, dob)

    total = compute_total(st.session_state.responses)

    st.subheader("결과")
    st.metric("MG-ADL 총점", f"{total} / 24")

    rows = []
    for item in ITEMS:
        sc = int(st.session_state.responses.get(item["id"], 0))
        rows.append({"문항": item["question"], "점수": sc, "선택": item["choices"][sc]})
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    st.divider()
    st.subheader("Google Sheets 누적 저장(append)")
    st.caption("‘결과 저장’을 누를 때마다 스프레드시트에 **새 행으로 누적** 저장됩니다.")

    # record 구성: EXPECTED_HEADER 키를 모두 포함
    record = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "name": name,
        "dob": dob,
        "patient_hash": ph,
        "total_score": total,
    }
    for it in ITEMS:
        record[it["id"]] = int(st.session_state.responses.get(it["id"], 0))

    with st.expander("연동 상태 점검"):
        st.write("- SHEET_ID 설정:", "✅" if bool(SHEET_ID) else "⛔ 없음")
        st.write("- GOOGLE_SERVICE_ACCOUNT 설정:", "✅" if (SA_INFO is not None) else "⛔ 없음")
        st.write("- WORKSHEET_NAME:", WORKSHEET_NAME)
        if SA_INFO and isinstance(SA_INFO, dict):
            st.write("- service account:", SA_INFO.get("client_email", "(unknown)"))
        st.caption("※ 구글시트 공유에서 서비스계정 이메일을 **편집자**로 추가했는지 확인하세요.")

    colA, colB = st.columns(2)
    with colA:
        save_clicked = st.button("💾 결과 저장(스프레드시트)", type="primary", disabled=st.session_state.saved)
    with colB:
        if st.button("다시 저장 가능하게(중복방지 해제)"):
            st.session_state.saved = False
            st.rerun()

    if save_clicked:
        try:
            info = append_record_to_sheet(record)
            st.session_state.saved = True
            st.success("저장 완료!")

            st.write("📌 저장된 위치")
            st.write("스프레드시트:", info["spreadsheet_title"])
            st.write("탭(워크시트):", info["worksheet_title"])
            if info["updated_range"]:
                st.write("업데이트 범위:", info["updated_range"])

        except Exception as e:
            st.error("저장 실패: Secrets 설정/시트 공유 권한/SHEET_ID/탭 이름을 확인하세요.")
            st.exception(e)

    st.divider()
    st.subheader("현재 결과 CSV 다운로드")
    export_df = pd.DataFrame([record])
    st.download_button(
        "⬇️ 현재 결과 CSV 다운로드",
        data=export_df.to_csv(index=False, encoding="utf-8-sig"),
        file_name=f"mgadl_{ph}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
    )

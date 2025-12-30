import streamlit as st
import pandas as pd
from datetime import datetime
import hashlib
import json

import gspread
from google.oauth2.service_account import Credentials


# =========================
# 설정 / Secrets
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

EXPECTED_HEADER = (
    ["created_at", "submission_id", "name", "dob", "patient_hash", "total_score"]
    + [it["id"] for it in ITEMS]
)


# =========================
# 유틸
# =========================
def compute_total(responses: dict) -> int:
    return int(sum(int(v) for v in responses.values()))


def make_patient_hash(name: str, dob: str) -> str:
    raw = f"{name}|{dob}|{SALT}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def make_submission_id(patient_hash: str, created_at: str, responses: dict) -> str:
    # 설문 한 번 제출(페이지2 완료 클릭) 단위를 고유하게 식별
    payload = json.dumps(responses, sort_keys=True, ensure_ascii=False)
    raw = f"{patient_hash}|{created_at}|{payload}".encode("utf-8")
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
        raise RuntimeError("Secrets에 SHEET_ID가 없습니다. (스프레드시트 ID만 입력)")
    gc = _get_gspread_client()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=2000, cols=100)
    return ws


def ensure_header(ws):
    values = ws.get_all_values()
    if len(values) == 0:
        ws.append_row(EXPECTED_HEADER, value_input_option="USER_ENTERED")
        return EXPECTED_HEADER

    current = ws.row_values(1)
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
    ws = get_worksheet()
    header = ensure_header(ws)
    row = [record.get(h, "") for h in header]
    res = ws.append_row(row, value_input_option="USER_ENTERED")

    updated_range = None
    if isinstance(res, dict):
        updated_range = res.get("updates", {}).get("updatedRange")

    return {
        "spreadsheet_title": ws.spreadsheet.title,
        "worksheet_title": ws.title,
        "updated_range": updated_range,
    }


# =========================
# 세션 상태
# =========================
if "step" not in st.session_state:
    st.session_state.step = 1  # 1: 인증+정보, 2: 설문, 3: 결과/전송

if "authed" not in st.session_state:
    st.session_state.authed = False

if "patient" not in st.session_state:
    st.session_state.patient = {"name": "", "dob": ""}

if "responses" not in st.session_state:
    st.session_state.responses = {}

if "created_at" not in st.session_state:
    st.session_state.created_at = ""  # 제출 시각(페이지2 완료 클릭 시 확정)

if "submission_id" not in st.session_state:
    st.session_state.submission_id = ""  # 중복방지용

if "sent" not in st.session_state:
    st.session_state.sent = False

if "send_info" not in st.session_state:
    st.session_state.send_info = None

if "send_error" not in st.session_state:
    st.session_state.send_error = None


def reset_all():
    st.session_state.step = 1
    st.session_state.authed = False
    st.session_state.patient = {"name": "", "dob": ""}
    st.session_state.responses = {}
    st.session_state.created_at = ""
    st.session_state.submission_id = ""
    st.session_state.sent = False
    st.session_state.send_info = None
    st.session_state.send_error = None


# =========================
# UI 공통
# =========================
st.title("🧠 MG-ADL 설문")
st.caption("하단의 ‘완료’ 버튼으로만 다음 단계로 넘어갑니다. (사이드바 이동 없음)")

progress_map = {1: 33, 2: 66, 3: 100}
st.progress(progress_map.get(st.session_state.step, 0))

top_col1, top_col2 = st.columns([1, 1])
with top_col1:
    st.write(f"현재 단계: **{st.session_state.step} / 3**")
with top_col2:
    if st.button("전체 초기화", type="secondary"):
        reset_all()
        st.rerun()

st.divider()


# =========================
# 1) 비밀번호 + 이름/생년월일
# =========================
if st.session_state.step == 1:
    st.header("1) 접속 인증 및 대상자 정보")

    with st.form("page1_form"):
        pw = st.text_input("접속 비밀번호", type="password", placeholder="0712")
        name = st.text_input("이름", value=st.session_state.patient["name"], placeholder="예: 홍길동")
        dob = st.date_input("생년월일", value=None)
        submitted = st.form_submit_button("완료 (설문으로 이동)")

    if submitted:
        if pw != APP_PASSWORD:
            st.error("비밀번호가 올바르지 않습니다.")
        elif not name.strip():
            st.error("이름을 입력해주세요.")
        elif dob is None:
            st.error("생년월일을 선택해주세요.")
        else:
            st.session_state.authed = True
            st.session_state.patient["name"] = name.strip()
            st.session_state.patient["dob"] = dob.isoformat()

            # 다음 단계로
            st.session_state.step = 2
            st.rerun()


# =========================
# 2) 설문 (하단 완료로 3페이지 이동)
# =========================
elif st.session_state.step == 2:
    st.header("2) MG-ADL 설문")

    if not st.session_state.authed:
        st.warning("인증 정보가 없습니다. 1단계로 돌아갑니다.")
        st.session_state.step = 1
        st.rerun()

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

        submitted = st.form_submit_button("완료 (결과/저장으로 이동)")

    if submitted:
        st.session_state.responses = new_responses

        # 제출 시각 확정 + 제출ID 생성(중복방지)
        created_at = datetime.now().isoformat(timespec="seconds")
        st.session_state.created_at = created_at

        ph = make_patient_hash(st.session_state.patient["name"], st.session_state.patient["dob"])
        st.session_state.submission_id = make_submission_id(ph, created_at, new_responses)

        # 새로운 제출이므로 전송 상태 초기화
        st.session_state.sent = False
        st.session_state.send_info = None
        st.session_state.send_error = None

        # 다음 단계로
        st.session_state.step = 3
        st.rerun()

    st.divider()
    if st.button("이전 (정보 수정)", type="secondary"):
        st.session_state.step = 1
        st.rerun()


# =========================
# 3) 결과 + 자동 전송(버튼 없음) + 상태 창
# =========================
else:
    st.header("3) 결과 및 저장 (자동 전송)")

    if not st.session_state.responses:
        st.warning("설문 응답이 없습니다. 2단계로 돌아갑니다.")
        st.session_state.step = 2
        st.rerun()

    name = st.session_state.patient["name"]
    dob = st.session_state.patient["dob"]
    ph = make_patient_hash(name, dob)

    total = compute_total(st.session_state.responses)

    st.subheader("결과")
    st.metric("MG-ADL 총점", f"{total} / 24")

    rows = []
    for item in ITEMS:
        sc = int(st.session_state.responses.get(item["id"], 0))
        rows.append({"문항": item["question"], "점수": sc, "선택": item["choices"][sc]})
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # 자동 전송 (중복방지: sent=True면 다시 append하지 않음)
    if not st.session_state.sent:
        record = {
            "created_at": st.session_state.created_at or datetime.now().isoformat(timespec="seconds"),
            "submission_id": st.session_state.submission_id or "",
            "name": name,
            "dob": dob,
            "patient_hash": ph,
            "total_score": total,
        }
        for it in ITEMS:
            record[it["id"]] = int(st.session_state.responses.get(it["id"], 0))

        try:
            info = append_record_to_sheet(record)
            st.session_state.sent = True
            st.session_state.send_info = info
            st.session_state.send_error = None
        except Exception as e:
            st.session_state.sent = False
            st.session_state.send_info = None
            st.session_state.send_error = repr(e)

    st.divider()
    st.subheader("전송 상태")

    if st.session_state.sent:
        st.success("전송 완료되었습니다. (중복 저장 방지 적용됨)")
        if st.session_state.send_info:
            st.caption(f"스프레드시트: {st.session_state.send_info.get('spreadsheet_title','')}")
            st.caption(f"탭(워크시트): {st.session_state.send_info.get('worksheet_title','')}")
            if st.session_state.send_info.get("updated_range"):
                st.caption(f"업데이트 범위: {st.session_state.send_info.get('updated_range')}")
    else:
        st.error("전송에 실패했습니다.")
        if st.session_state.send_error:
            st.code(st.session_state.send_error)
        # 전송 버튼은 없애되, 실패 시에만 '재시도'는 필요하니 제공(운영상 필수)
        if st.button("전송 재시도", type="primary"):
            st.session_state.sent = False
            st.rerun()

    st.divider()
    colA, colB = st.columns(2)
    with colA:
        if st.button("이전 (설문 수정)", type="secondary"):
            st.session_state.step = 2
            st.rerun()
    with colB:
        if st.button("새 설문 시작", type="secondary"):
            reset_all()
            st.rerun()


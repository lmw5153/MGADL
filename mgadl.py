import streamlit as st
import pandas as pd
from datetime import datetime, date
import hashlib
import json

import gspread
from google.oauth2.service_account import Credentials


# =========================
# 설정 / Secrets
# =========================
st.set_page_config(page_title="MG-ADL 설문", page_icon="🧠", layout="centered")

APP_PASSWORD = st.secrets.get("APP_PASSWORD", "")  # 비번은 Secrets에서만 관리 (화면 힌트 없음)
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
        raise RuntimeError("Secrets에 SHEET_ID가 없습니다. (URL 말고 ID만)")
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
        ws.append_row(EXPECTED_HEADER, value_input_option="RAW")
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
    """
    핵심: value_input_option='RAW' + 숫자값은 int로 넣어야
         구글시트에서 '정수(숫자)'로 저장됨.
    """
    ws = get_worksheet()
    header = ensure_header(ws)

    row = [record.get(h, "") for h in header]

    # RAW로 append (정수는 정수로 들어감)
    res = ws.append_row(row, value_input_option="RAW")

    updated_range = None
    if isinstance(res, dict):
        updated_range = res.get("updates", {}).get("updatedRange")

    return {
        "spreadsheet_title": ws.spreadsheet.title,
        "worksheet_title": ws.title,
        "updated_range": updated_range,
    }


def build_record():
    name = st.session_state.patient["name"]
    dob = st.session_state.patient["dob"]
    responses = st.session_state.responses

    ph = make_patient_hash(name, dob)
    total = compute_total(responses)

    created_at = st.session_state.created_at
    submission_id = st.session_state.submission_id

    # 점수/총점은 반드시 int로
    record = {
        "created_at": created_at,
        "submission_id": submission_id,
        "name": name,
        "dob": dob,
        "patient_hash": ph,
        "total_score": int(total),
    }
    for it in ITEMS:
        record[it["id"]] = int(responses.get(it["id"], 0))
    return record


def try_send():
    """중복 방지: 같은 submission_id는 1번만 전송(sent=True이면 재전송 안 함)"""
    if st.session_state.sent:
        return True
    info = append_record_to_sheet(build_record())
    st.session_state.sent = True
    st.session_state.send_info = info
    st.session_state.send_error = None
    return True


# =========================
# 세션 상태
# =========================
if "step" not in st.session_state:
    st.session_state.step = 1  # 1: 비번+정보, 2: 설문, 3: 전송완료/결과

if "authed" not in st.session_state:
    st.session_state.authed = False

if "patient" not in st.session_state:
    st.session_state.patient = {"name": "", "dob": ""}

if "responses" not in st.session_state:
    st.session_state.responses = {}

if "created_at" not in st.session_state:
    st.session_state.created_at = ""

if "submission_id" not in st.session_state:
    st.session_state.submission_id = ""

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
st.caption("하단 ‘완료’ 버튼으로만 다음 단계로 이동합니다.")
progress_map = {1: 33, 2: 66, 3: 100}
st.progress(progress_map.get(st.session_state.step, 0))

c1, c2 = st.columns([1, 1])
with c1:
    st.write(f"현재 단계: **{st.session_state.step} / 3**")
with c2:
    if st.button("전체 초기화"):
        reset_all()
        st.rerun()

st.divider()


# =========================
# 1) 비밀번호 + 이름/생년월일 (힌트 없음)
# =========================
if st.session_state.step == 1:
    st.header("1) 접속 인증 및 대상자 정보")

    with st.form("page1_form"):
        pw = st.text_input("접속 비밀번호", type="password")  # 힌트/placeholder 없음
        name = st.text_input("이름", value=st.session_state.patient["name"], placeholder="예: 홍길동")

        dob = st.date_input(
            "생년월일",
            value=None,
            min_value=date(1900, 1, 1),
            max_value=date(2050, 12, 31),
        )

        submitted = st.form_submit_button("완료 (설문으로 이동)")

    if submitted:
        if not APP_PASSWORD:
            st.error("서버 설정 오류: APP_PASSWORD가 Secrets에 설정되어 있지 않습니다.")
        elif pw != APP_PASSWORD:
            st.error("비밀번호가 올바르지 않습니다.")
        elif not name.strip():
            st.error("이름을 입력해주세요.")
        elif dob is None:
            st.error("생년월일을 선택해주세요.")
        else:
            st.session_state.authed = True
            st.session_state.patient["name"] = name.strip()
            st.session_state.patient["dob"] = dob.isoformat()
            st.session_state.step = 2
            st.rerun()


# =========================
# 2) 설문 (완료 누르면 즉시 전송 성공해야 3페이지 이동)
# =========================
elif st.session_state.step == 2:
    st.header("2) MG-ADL 설문")

    if not st.session_state.authed:
        st.warning("인증 정보가 없습니다. 1단계로 돌아갑니다.")
        st.session_state.step = 1
        st.rerun()

    st.write(f"대상자: **{st.session_state.patient['name']}** (DOB: {st.session_state.patient['dob']})")

    if st.session_state.send_error:
        st.error("이전 전송이 실패했습니다.")
        st.code(st.session_state.send_error)

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

        submitted = st.form_submit_button("완료 (전송 후 결과 페이지로 이동)")

    if submitted:
        st.session_state.responses = new_responses

        created_at = datetime.now().isoformat(timespec="seconds")
        st.session_state.created_at = created_at

        ph = make_patient_hash(st.session_state.patient["name"], st.session_state.patient["dob"])
        st.session_state.submission_id = make_submission_id(ph, created_at, new_responses)

        st.session_state.sent = False
        st.session_state.send_info = None
        st.session_state.send_error = None

        with st.spinner("전송 중입니다… (전송 완료 전에는 페이지가 넘어가지 않습니다)"):
            try:
                try_send()
                st.session_state.step = 3
                st.rerun()
            except Exception as e:
                st.session_state.sent = False
                st.session_state.send_info = None
                st.session_state.send_error = repr(e)
                st.error("전송 실패: 설정/권한/시트 ID를 확인하세요.")
                st.code(st.session_state.send_error)

    st.divider()
    if st.button("이전 (정보 수정)"):
        st.session_state.step = 1
        st.rerun()

    if st.session_state.send_error and st.button("전송 재시도"):
        with st.spinner("전송 재시도 중…"):
            try:
                st.session_state.sent = False
                st.session_state.send_info = None
                st.session_state.send_error = None
                try_send()
                st.session_state.step = 3
                st.rerun()
            except Exception as e:
                st.session_state.send_error = repr(e)
                st.error("재시도 전송 실패")
                st.code(st.session_state.send_error)


# =========================
# 3) 전송 완료 페이지(여기서는 전송 안 함) + 결과 표시
# =========================
else:
    st.header("3) 전송 완료 및 결과")

    if not st.session_state.sent:
        st.warning("전송 완료 상태가 아닙니다. 2단계로 돌아갑니다.")
        st.session_state.step = 2
        st.rerun()

    record = build_record()
    total = int(record["total_score"])

    st.success("✅ 전송이 완료되었습니다. (중복 저장 방지 적용)")

    if st.session_state.send_info:
        st.caption(f"스프레드시트: {st.session_state.send_info.get('spreadsheet_title','')}")
        st.caption(f"탭(워크시트): {st.session_state.send_info.get('worksheet_title','')}")
        if st.session_state.send_info.get("updated_range"):
            st.caption(f"업데이트 범위: {st.session_state.send_info.get('updated_range')}")

    st.divider()
    st.subheader("결과")
    st.metric("MG-ADL 총점", f"{total} / 24")

    rows = []
    for item in ITEMS:
        sc = int(st.session_state.responses.get(item["id"], 0))
        rows.append({"문항": item["question"], "점수": sc, "선택": item["choices"][sc]})
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    st.divider()
    colA, colB = st.columns(2)
    with colA:
        if st.button("이전 (설문 수정)"):
            st.session_state.step = 2
            st.rerun()
    with colB:
        if st.button("새 설문 시작"):
            reset_all()
            st.rerun()



import json
import os
import re
import time
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import streamlit as st
import streamlit.components.v1 as components
from audio_recorder_streamlit import audio_recorder
from dotenv import load_dotenv
from openai import OpenAI

# =========================================================
# 1. 기본 설정
# =========================================================
load_dotenv()
client = OpenAI()

st.set_page_config(
    page_title="시작이 제일 무서운 미룬이의 나무",
    page_icon="🌳",
    layout="wide",
)

STATE_FILE = "tree_state.json"

TREE_SHOP_ITEMS = [
    {"name": "🪴나무 울타리", "price": 50, "desc": "나무 주변을 꾸며주는 울타리"},
    {"name": "🚿물뿌리개", "price": 80, "desc": "나무 감성을 더하는 장식"},
    {"name": "💚희귀 씨앗", "price": 120, "desc": "특별한 씨앗 장식"},
    {"name": "💐꽃", "price": 70, "desc": "나무 주변을 꾸며주는 꽃"},
]

DEFAULT_GAME_STATE = {
    "view": "tree",  # tree | shop
    "messages": [
        {
            "role": "assistant",
            "content": (
                "안녕, 오늘 할 일을 말해줄래?.\n"
                "내가 지금 너의 할 일을 당장 시작할 수 있게 5단계 퀘스트로 쪼개줄 거야. 퀘스트를 실행하면 나무가 점점 자라는 걸 볼 수 있어."
            ),
        }
    ],
    "task_title": "",
    "task_context": "",
    "deadline_raw": "",
    "deadline_label": "미설정",
    "urgency": "정보 없음",
    "inferred_state": "분석 전",
    "quests": [],
    "step": 0,
    "coins": 0,
    "inventory": [],
    "last_action_time": time.time(),
    "show_popup": False,
    "recent_summaries": [],
}


# =========================================================
# 2. 상태 저장 / 불러오기 / 초기화
# =========================================================
def load_game_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception:
            return deepcopy(DEFAULT_GAME_STATE)
    return deepcopy(DEFAULT_GAME_STATE)


def save_game_state() -> None:
    game_to_save = deepcopy(st.session_state.game)
    game_to_save["last_action_time"] = time.time()
    game_to_save["show_popup"] = False

    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(game_to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.warning(f"상태 저장 중 문제가 생겼어: {e}")


def reset_game_state() -> None:
    st.session_state.game = deepcopy(DEFAULT_GAME_STATE)
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


# =========================================================
# 3. 세션 상태 초기화
# =========================================================
def init_session_state() -> None:
    if "game" not in st.session_state:
        st.session_state.game = load_game_state()


init_session_state()


# =========================================================
# 4. 유틸 함수
# =========================================================
def add_message(role: str, content: str) -> None:
    st.session_state.game["messages"].append({"role": role, "content": content})


def reset_idle_timer() -> None:
    st.session_state.game["last_action_time"] = time.time()
    st.session_state.game["show_popup"] = False
    save_game_state()


def parse_deadline(deadline_text: str) -> Optional[datetime]:
    now = datetime.now()
    deadline_text = deadline_text.strip()

    if not deadline_text or deadline_text == "미설정":
        return None

    if deadline_text.endswith("시간 뒤"):
        try:
            hours = int(deadline_text.replace("시간 뒤", "").strip())
            return now + timedelta(hours=hours)
        except ValueError:
            return None

    if deadline_text == "오늘":
        return datetime.combine(now.date(), datetime.max.time())

    if deadline_text == "내일":
        tomorrow = now.date() + timedelta(days=1)
        return datetime.combine(tomorrow, datetime.max.time())

    if deadline_text.endswith("일 뒤"):
        try:
            days = int(deadline_text.replace("일 뒤", "").strip())
            target_date = now.date() + timedelta(days=days)
            return datetime.combine(target_date, datetime.max.time())
        except ValueError:
            return None

    try:
        return datetime.strptime(deadline_text, "%Y-%m-%d")
    except ValueError:
        return None


def pretty_deadline(deadline_text: str) -> str:
    deadline_dt = parse_deadline(deadline_text)
    if deadline_dt is None:
        return "미설정"
    return deadline_dt.strftime("%Y-%m-%d %H:%M")


def current_quest() -> Optional[str]:
    quests = st.session_state.game["quests"]
    step = st.session_state.game["step"]
    if 0 <= step < len(quests):
        return quests[step]
    return None


def is_progress_report(user_text: str) -> bool:
    keywords = ["완료", "했어", "끝냈어", "끝", "다음", "성공", "해냈어", "완수"]
    return any(k in user_text for k in keywords)


def is_reset_request(user_text: str) -> bool:
    keywords = ["다시", "새 목표", "새로", "리셋", "목표 바꿀래", "계획 다시"]
    return any(k in user_text for k in keywords)


def reward_for_step(step_number: int) -> int:
    reward_table = {
        1: 10,
        2: 15,
        3: 20,
        4: 25,
        5: 30,
    }
    return reward_table.get(step_number, 0)


def parse_quests(raw_quests: Any) -> List[str]:
    if isinstance(raw_quests, list):
        return [str(q).strip() for q in raw_quests if str(q).strip()][:5]

    if isinstance(raw_quests, str):
        lines = [line.strip() for line in raw_quests.splitlines() if line.strip()]
        cleaned = []
        for line in lines:
            line = re.sub(r"^\s*\d+[\.\)]\s*", "", line)
            line = re.sub(r"^\s*[-*]\s*", "", line)
            if line:
                cleaned.append(line)
        return cleaned[:5]

    return []


# =========================================================
# 5. OpenAI 호출
# =========================================================
def transcribe_audio_to_text(audio_bytes: bytes) -> str:
    temp_path = "temp_audio.wav"
    with open(temp_path, "wb") as f:
        f.write(audio_bytes)

    with open(temp_path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
        )

    return transcript.text.strip()


def generate_plan_with_llm(user_goal: str) -> Dict[str, Any]:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    system_prompt = """
당신은 '시작이 제일 무서운 미룬이의 나무' 게임의 계획 코치입니다.

역할:
- 사용자가 하기 싫은 일도 지금 당장 시작할 수 있도록 돕는다.
- 사용자의 상태를 짧게 추론한다.
- 목표를 5단계의 아주 구체적인 퀘스트로 나눈다.

규칙:
- 반드시 5개의 퀘스트를 만든다.
- 각 퀘스트는 지금 당장 실행 가능한 행동이어야 한다.
- '준비하기', '열심히 하기', '집중하기'처럼 추상적인 표현은 금지한다.
- 마지막 5단계는 저장, 제출, 정리, 마무리 혹은 그와 비슷해야 한다.
- 사용자의 상태가 피곤하거나 부담이 크면 첫 단계는 아주 작게 만든다.
- 데드라인이 촉박하면 필수 산출물 중심으로 구성한다.
- 말투는 부드럽고 게임 코치 같아야 하며, 지나치게 독설적이면 안 된다.
- 출력은 반드시 JSON 하나만 반환한다.

JSON 형식:
{
  "task_title": "짧은 목표명",
  "inferred_state": "사용자의 상태를 짧게 요약",
  "deadline_label": "오늘/내일/3일 뒤/미설정/날짜",
  "urgency": "매우 높음/중간/낮음/정보 없음",
  "quests": ["...", "...", "...", "...", "..."],
  "coach_message": "짧은 코치 메시지"
}
""".strip()

    user_prompt = f"""
현재 시각: {now_str}
사용자 목표/입력: {user_goal}

주의:
- 사용자가 데드라인을 직접 말하지 않으면 deadline_label은 "미설정"으로 둔다.
- deadline_label과 urgency는 사용자의 입력을 기준으로 판단한다.
""".strip()

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
        max_tokens=700,
    )

    data = json.loads(response.choices[0].message.content)

    data.setdefault("task_title", "새 퀘스트")
    data.setdefault("inferred_state", "분석 전")
    data.setdefault("deadline_label", "미설정")
    data.setdefault("urgency", "정보 없음")
    data.setdefault("quests", [])
    data.setdefault("coach_message", "좋아, 한 단계씩 나무를 키워보자.")

    data["quests"] = parse_quests(data["quests"])
    while len(data["quests"]) < 5:
        data["quests"].append("작은 행동 한 단계 더 정리하기")

    return data


def summarize_session_with_llm(task_title: str, completed_count: int, total_coins: int) -> str:
    system_prompt = """
당신은 게임 세션 회고 도우미입니다.

규칙:
- 2문장 이내로 짧게 작성한다.
- 비난하지 않는다.
- 완료한 점을 먼저 짚고, 다음에 시도할 작은 행동 하나를 제안한다.
- 말투는 부드럽고 게임 코치처럼 한다.
""".strip()

    user_prompt = f"""
목표: {task_title}
완료 단계 수: {completed_count}
누적 코인: {total_coins}
""".strip()

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=200,
    )

    return response.choices[0].message.content.strip()


# =========================================================
# 6. 게임 로직
# =========================================================
def start_new_plan(user_text: str) -> None:
    plan = generate_plan_with_llm(user_text)

    st.session_state.game["task_title"] = plan["task_title"]
    st.session_state.game["task_context"] = user_text
    st.session_state.game["deadline_raw"] = plan["deadline_label"]
    st.session_state.game["deadline_label"] = pretty_deadline(plan["deadline_label"])
    st.session_state.game["urgency"] = plan["urgency"] if plan["deadline_label"] != "미설정" else "정보 없음"
    st.session_state.game["inferred_state"] = plan["inferred_state"]
    st.session_state.game["quests"] = plan["quests"]
    st.session_state.game["step"] = 0

    task_lines = "\n".join([f"{i+1}. {q}" for i, q in enumerate(plan["quests"])])

    assistant_msg = (
        f"🌳 **오늘의 나무 목표: {plan['task_title']}**\n\n"
        f"- 현재 상태: {plan['inferred_state']}\n"
        f"- 데드라인: {st.session_state.game['deadline_label']}\n"
        f"- 긴급도: {st.session_state.game['urgency']}\n\n"
        f"{plan['coach_message']}\n\n"
        f"**5단계 성장 계획**\n{task_lines}"
    )
    add_message("assistant", assistant_msg)
    save_game_state()


def complete_current_step() -> None:
    if not st.session_state.game["quests"]:
        add_message("assistant", "아직 심어진 목표가 없어. 먼저 오늘 할 일을 하나 정해보자.")
        save_game_state()
        return

    if st.session_state.game["step"] >= len(st.session_state.game["quests"]):
        add_message("assistant", "이미 이 나무는 다 자랐어. 새 목표를 심어볼까?")
        save_game_state()
        return

    finished_quest = st.session_state.game["quests"][st.session_state.game["step"]]
    step_number = st.session_state.game["step"] + 1
    reward = reward_for_step(step_number)

    st.session_state.game["step"] += 1
    st.session_state.game["coins"] += reward

    if st.session_state.game["step"] == len(st.session_state.game["quests"]):
        st.session_state.game["coins"] += 40

        summary = summarize_session_with_llm(
            task_title=st.session_state.game["task_title"],
            completed_count=len(st.session_state.game["quests"]),
            total_coins=st.session_state.game["coins"],
        )
        st.session_state.game["recent_summaries"].append(summary)

        add_message(
            "assistant",
            (
                f"🍎 **완주!** 방금 끝낸 단계는 **{finished_quest}**였어.\n"
                f"나무가 끝까지 자라서 열매를 맺었어. 완주 보너스까지 획득! (+{reward + 40} 코인)\n\n"
                f"{summary}"
            ),
        )
    else:
        next_q = current_quest()
        add_message(
            "assistant",
            (
                f"🌱 좋아, **{finished_quest}** 완료.\n"
                f"나무가 한 단계 자랐어. (+{reward} 코인)\n\n"
                f"다음 퀘스트는 **{next_q}**야."
            ),
        )

    save_game_state()


def handle_user_input(user_text: str) -> None:
    user_text = user_text.strip()
    if not user_text:
        return

    reset_idle_timer()
    add_message("user", user_text)
    save_game_state()

    no_active_plan = len(st.session_state.game["quests"]) == 0
    finished_plan = (
        len(st.session_state.game["quests"]) > 0
        and st.session_state.game["step"] >= len(st.session_state.game["quests"])
    )

    if no_active_plan or finished_plan or is_reset_request(user_text):
        start_new_plan(user_text)
        return

    if is_progress_report(user_text):
        complete_current_step()
        return

    if current_quest():
        add_message(
            "assistant",
            (
                f"지금 진행 중인 작업은 **{current_quest()}**야.\n"
                f"끝났으면 '완료'라고 말해주고, 목표를 바꾸고 싶으면 '다시'라고 말해줘."
            ),
        )
    else:
        add_message("assistant", "좋아, 새 목표를 심어보자.")

    save_game_state()


def buy_item(item: Dict[str, Any]) -> None:
    if st.session_state.game["coins"] < item["price"]:
        st.warning("코인이 부족해.")
        return

    if item["name"] in st.session_state.game["inventory"]:
        st.info("이미 가지고 있는 장식이야.")
        return

    st.session_state.game["coins"] -= item["price"]
    st.session_state.game["inventory"].append(item["name"])
    save_game_state()
    st.success(f"{item['name']} 구매 완료!")


# =========================================================
# 7. 공통 스타일
# =========================================================
st.markdown(
    """
    <style>
    .stApp {
        background: linear-gradient(180deg, #eef5e8 0%, #e4edd9 100%);
    }

    .top-card {
        background: #fffdf7;
        border-radius: 24px;
        padding: 18px 22px;
        box-shadow: 0 10px 28px rgba(0,0,0,0.06);
        border: 1px solid #e8e0cf;
        margin-bottom: 18px;
    }

    .status-card, .shop-card {
        background: #fffdf8;
        border: 1px solid #e5dcc8;
        border-radius: 18px;
        padding: 16px 18px;
        box-shadow: 0 6px 18px rgba(0,0,0,0.04);
        margin-top: 16px;
        margin-bottom: 14px;
    }

    .idle-popup {
        position: fixed;
        top: 15%;
        left: 50%;
        transform: translateX(-50%);
        z-index: 9999;
        background: #fff3cd;
        color: #5c4600;
        border: 1px solid #eed37b;
        border-radius: 18px;
        padding: 22px;
        box-shadow: 0 14px 32px rgba(0,0,0,0.18);
        width: 420px;
        text-align: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 8. 상단 헤더
# =========================================================
top_left, top_right = st.columns([3, 1])

with top_left:
    st.markdown(
        """
        <div class="top-card">
            <h1 style="margin-bottom: 0;">🪴 시작이 제일 무서운 미룬이의 나무 🪴</h1>
            <p style="margin-top: 6px; color: #6b7280;">
                하기 싫은 일을 작은 행동으로 쪼개서 한 그루 나무를 키우는 게임형 생산성 앱
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

with top_right:
    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
    nav1, nav2, nav3 = st.columns(3)

    with nav1:
        if st.button("🪴 나무", use_container_width=True):
            st.session_state.game["view"] = "tree"
            save_game_state()
            st.rerun()

    with nav2:
        if st.button("🛒 상점", use_container_width=True):
            st.session_state.game["view"] = "shop"
            save_game_state()
            st.rerun()

    with nav3:
        if st.button("🔄 초기화", use_container_width=True):
            reset_game_state()
            st.rerun()


# =========================================================
# 9. 무응답 팝업
# =========================================================
if time.time() - st.session_state.game["last_action_time"] > 60:
    st.session_state.game["show_popup"] = True

if st.session_state.game["show_popup"]:
    st.markdown(
        """
        <div class="idle-popup">
            <h3>🌾오랫동안 입력을 하지 않았군</h3>
            <p>한동안 입력이 없었어. 다시 시작해볼까?.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("다시 시작"):
        reset_idle_timer()
        st.rerun()


# =========================================================
# 10. 나무 HTML 렌더링
# =========================================================
def render_tree_html(stage: int, inventory: List[str]) -> str:
    extra = ""

    if "나무 울타리" in inventory:
        extra += '<div class="fence"></div>'

    if "꽃 화분" in inventory:
        extra += '<div class="pot pot-left"></div><div class="pot pot-right"></div>'

    if "물뿌리개" in inventory:
        extra += '<div class="watering-can"></div>'

    if "희귀 씨앗" in inventory:
        extra += '<div class="rare-seed-glow"></div>'

    if stage == 0:
        extra += '<div class="seed"></div>'

    if stage >= 1:
        extra += '<div class="tree-trunk"></div>'

    if stage >= 2:
        extra += """
        <div class="tree-top small">
            <div class="blob b1"></div>
            <div class="blob b2"></div>
            <div class="blob b3"></div>
        </div>
        """

    if stage >= 3:
        extra += """
        <div class="tree-top medium">
            <div class="blob b1"></div>
            <div class="blob b2"></div>
            <div class="blob b3"></div>
            <div class="blob b4"></div>
        </div>
        """

    if stage >= 4:
        extra += """
        <div class="tree-top large">
            <div class="blob b1"></div>
            <div class="blob b2"></div>
            <div class="blob b3"></div>
            <div class="blob b4"></div>
            <div class="blob b5"></div>
        </div>
        <div class="flower flower-1"></div>
        <div class="flower flower-2"></div>
        <div class="flower flower-3"></div>
        """

    if stage >= 5:
        extra += """
        <div class="fruit fruit-1"></div>
        <div class="fruit fruit-2"></div>
        <div class="fruit fruit-3"></div>
        """

    return f"""
    <html>
    <head>
    <style>
        body {{
            margin: 0;
            padding: 0;
            background: transparent;
            overflow: hidden;
            font-family: sans-serif;
        }}

        .tree-panel {{
            background: linear-gradient(180deg, #f8fbf5 0%, #edf4e7 100%);
            border-radius: 30px;
            padding: 26px;
            border: 1px solid #dde6d6;
            min-height: 420px;
            box-sizing: border-box;
        }}

        .tree-header {{
            width: 100%;
            height: 48px;
            border-radius: 999px;
            background: linear-gradient(90deg, #dfead4 0%, #edf4e7 100%);
            border: 1px solid #d3dfc6;
            margin-bottom: 24px;
        }}

        .tree-ground {{
            position: relative;
            width: 100%;
            height: 300px;
            border-radius: 26px;
            overflow: hidden;
            border: 2px solid #d6ccb6;
            box-sizing: border-box;
            background: linear-gradient(
                180deg,
                #eaf4ff 0%,
                #f7fbff 36%,
                #eef6ed 47%,
                #c9bb97 48%,
                #9a744e 49%,
                #7a5738 100%
            );
        }}

        .tree-ground::before {{
            content: "";
            position: absolute;
            left: 0;
            right: 0;
            bottom: 0;
            height: 42%;
            background:
                repeating-linear-gradient(
                    90deg,
                    rgba(98, 64, 35, 0.10) 0px,
                    rgba(98, 64, 35, 0.10) 26px,
                    rgba(255,255,255,0.02) 26px,
                    rgba(255,255,255,0.02) 58px
                );
        }}

        .ground-line {{
            position: absolute;
            left: 0;
            right: 0;
            top: 48%;
            height: 2px;
            background: rgba(116, 93, 63, 0.10);
        }}

        .seed {{
            position: absolute;
            left: 50%;
            top: 61%;
            transform: translate(-50%, -50%);
            width: 26px;
            height: 26px;
            border-radius: 50%;
            background: radial-gradient(circle at 35% 35%, #7a5536, #5c3c26);
            box-shadow: 0 6px 10px rgba(0,0,0,0.12);
        }}

        .rare-seed-glow {{
            position: absolute;
            left: 50%;
            top: 61%;
            transform: translate(-50%, -50%);
            width: 76px;
            height: 76px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(255,226,134,0.28), rgba(255,226,134,0.0) 72%);
        }}

        .tree-trunk {{
            position: absolute;
            left: 50%;
            bottom: 15%;
            transform: translateX(-50%);
            width: 24px;
            height: 120px;
            border-radius: 16px;
            background: linear-gradient(180deg, #8b6642, #6f4f35);
            box-shadow:
                inset 0 2px 0 rgba(255,255,255,0.10),
                0 6px 12px rgba(0,0,0,0.10);
        }}

        .tree-top {{
            position: absolute;
            left: 50%;
            transform: translateX(-50%);
        }}

        .tree-top.small {{
            bottom: 39%;
            width: 110px;
            height: 90px;
        }}

        .tree-top.medium {{
            bottom: 36%;
            width: 150px;
            height: 118px;
        }}

        .tree-top.large {{
            bottom: 31%;
            width: 190px;
            height: 150px;
        }}

        .blob {{
            position: absolute;
            border-radius: 50%;
            background: radial-gradient(circle at 35% 35%, #a7db88, #5f9850);
            box-shadow: 0 8px 16px rgba(0,0,0,0.10);
        }}

        .tree-top.small .b1 {{ width: 56px; height: 56px; left: 12px; top: 24px; }}
        .tree-top.small .b2 {{ width: 58px; height: 58px; left: 42px; top: 10px; }}
        .tree-top.small .b3 {{ width: 52px; height: 52px; left: 58px; top: 30px; }}

        .tree-top.medium .b1 {{ width: 68px; height: 68px; left: 8px; top: 34px; }}
        .tree-top.medium .b2 {{ width: 76px; height: 76px; left: 38px; top: 8px; }}
        .tree-top.medium .b3 {{ width: 66px; height: 66px; left: 80px; top: 34px; }}
        .tree-top.medium .b4 {{ width: 54px; height: 54px; left: 50px; top: 52px; }}

        .tree-top.large .b1 {{ width: 84px; height: 84px; left: 8px; top: 44px; }}
        .tree-top.large .b2 {{ width: 92px; height: 92px; left: 42px; top: 8px; }}
        .tree-top.large .b3 {{ width: 86px; height: 86px; left: 96px; top: 36px; }}
        .tree-top.large .b4 {{ width: 68px; height: 68px; left: 118px; top: 72px; }}
        .tree-top.large .b5 {{ width: 64px; height: 64px; left: 46px; top: 74px; }}

        .flower, .fruit {{
            position: absolute;
            border-radius: 50%;
            box-shadow: 0 4px 8px rgba(0,0,0,0.10);
        }}

        .flower {{
            width: 16px;
            height: 16px;
            background: radial-gradient(circle at center, #ffe07a 0 28%, #f7b7c9 29% 100%);
        }}

        .flower-1 {{ left: 46%; bottom: 57%; }}
        .flower-2 {{ left: 52%; bottom: 63%; }}
        .flower-3 {{ left: 58%; bottom: 55%; }}

        .fruit {{
            width: 20px;
            height: 20px;
            background: radial-gradient(circle at 35% 35%, #ffcd80, #d9892b);
        }}

        .fruit-1 {{ left: 45%; bottom: 55%; }}
        .fruit-2 {{ left: 52%; bottom: 61%; }}
        .fruit-3 {{ left: 58%; bottom: 53%; }}

        .fence {{
            position: absolute;
            left: 10%;
            right: 10%;
            bottom: 10%;
            height: 34px;
            border-bottom: 3px solid #8d6846;
        }}

        .fence::before {{
            content: "";
            position: absolute;
            left: 0;
            right: 0;
            top: 0;
            height: 100%;
            background:
                repeating-linear-gradient(
                    90deg,
                    transparent 0 10px,
                    #d2ad7a 10px 18px,
                    transparent 18px 32px
                );
        }}

        .pot {{
            position: absolute;
            bottom: 12%;
            width: 38px;
            height: 28px;
            background: linear-gradient(180deg, #c98a57, #95552c);
            border-radius: 0 0 12px 12px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.10);
        }}

        .pot-left {{ left: 18%; }}
        .pot-right {{ right: 18%; }}

        .watering-can {{
            position: absolute;
            left: 14%;
            bottom: 14%;
            width: 40px;
            height: 24px;
            border-radius: 12px;
            background: linear-gradient(180deg, #93b8d3, #5f84a0);
        }}

        .watering-can::before {{
            content: "";
            position: absolute;
            right: -10px;
            top: 7px;
            width: 14px;
            height: 5px;
            border-radius: 5px;
            background: #5f84a0;
            transform: rotate(-18deg);
        }}

        .watering-can::after {{
            content: "";
            position: absolute;
            left: 8px;
            top: -9px;
            width: 16px;
            height: 10px;
            border: 3px solid #5f84a0;
            border-bottom: none;
            border-radius: 12px 12px 0 0;
        }}
    </style>
    </head>
    <body>
        <div class="tree-panel">
            <div class="tree-header"></div>
            <div class="tree-ground">
                <div class="ground-line"></div>
                {extra}
            </div>
        </div>
    </body>
    </html>
    """

# =========================================================
# 10. 렌더링 함수
# =========================================================
def render_tree_panel() -> None:
    st.subheader("나무를 키워봐")

    step = st.session_state.game["step"]
    quests = st.session_state.game["quests"]
    inventory = st.session_state.game["inventory"]
    tree_stage = min(step, 5)

    components.html(
        render_tree_html(tree_stage, inventory),
        height=430,
        scrolling=False,
    )

    current_text = current_quest() if quests and step < len(quests) else "현재 진행 중인 작업 없음"
    inventory_text = ", ".join(inventory) if inventory else "없음"

    st.markdown(
        f"""
        <div class="status-card">
            <b>현재 목표</b>: {st.session_state.game["task_title"] or "아직 없음"}<br>
            <b>현재 상태</b>: {st.session_state.game["inferred_state"]}<br>
            <b>데드라인</b>: {st.session_state.game["deadline_label"]}<br>
            <b>긴급도</b>: {st.session_state.game["urgency"]}<br>
            <b>현재 단계</b>: {step} / 5<br>
            <b>현재 작업</b>: {current_text}<br>
            <b>코인</b>: {st.session_state.game["coins"]}<br>
            <b>보유 장식</b>: {inventory_text}
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("성장 단계")
    if quests:
        for i, q in enumerate(quests):
            if i < step:
                st.write(f"✅ ~~{q}~~")
            elif i == step:
                st.success(f"현재 작업: {q}")
            else:
                st.write(f"▫️ {q}")
    else:
        st.info("아직 심어진 목표가 없어. 새 목표를 입력해줘.")


def render_chat() -> None:
    st.subheader("코치")
    chat_box = st.container(height=460, border=True)
    with chat_box:
        for m in st.session_state.game["messages"]:
            avatar = "🌳" if m["role"] == "assistant" else "👤"
            with st.chat_message(m["role"], avatar=avatar):
                st.write(m["content"])


def render_shop() -> None:
    st.subheader("상점")
    st.write("여기서 나무를 더 자라게 하고 꾸밀 수 있는 아이템을 코인으로 살 수 있어.")

    left, right = st.columns([1, 3])

    with left:
        st.metric("코인", st.session_state.game.get("coins", 0))

        if st.button("🪴 나무로 돌아가기", use_container_width=True):
            st.session_state.game["view"] = "tree"
            save_game_state()
            st.rerun()

    with right:
        cols = st.columns(2)
        for idx, item in enumerate(TREE_SHOP_ITEMS):
            with cols[idx % 2]:
                st.markdown(
                    f"""
                    <div class="shop-card">
                        <h4 style="margin-bottom: 6px;">{item['name']}</h4>
                        <p style="margin-top: 0; color: #6b7280;">{item['desc']}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                st.write(f"가격: {item['price']} 코인")
                can_buy = st.session_state.game.get("coins", 0) >= item["price"]
                already_owned = item["name"] in st.session_state.game["inventory"]

                if already_owned:
                    st.button(f"{item['name']} 보유 중", key=f"owned_{idx}", disabled=True)
                else:
                    if st.button(f"{item['name']} 구매", key=f"buy_{idx}", disabled=not can_buy):
                        buy_item(item)
                        st.rerun()

    st.divider()
    st.subheader("보유 아이템")

    inventory = st.session_state.game.get("inventory", [])
    if inventory:
        for item in inventory:
            st.write(f"✅ {item}")
    else:
        st.info("아직 구매한 아이템이 없어.")


# =========================================================
# 11. 화면 분기
# =========================================================
if st.session_state.game["view"] == "tree":
    left, right = st.columns([1.2, 1], gap="large")

    with left:
        render_tree_panel()

    with right:
        render_chat()
        st.markdown("---")

        col1, col2 = st.columns([1, 5])

        audio_data = None
        text_input = None

        with col1:
            audio_data = audio_recorder(
                text="",
                icon_size="2x",
                neutral_color="#5f8f52",
                recording_color="#d9534f",
                key="audio_input",
            )

        with col2:
            text_input = st.chat_input("할 일, 현재 상태, 데드라인 등을 입력해줘.")

    incoming_text = ""

    if audio_data:
        try:
            incoming_text = transcribe_audio_to_text(audio_data)
        except Exception as e:
            add_message("assistant", f"음성 인식 중 문제가 생겼어: {e}")
            st.rerun()
    elif text_input:
        incoming_text = text_input.strip()

    if incoming_text:
        handle_user_input(incoming_text)
        st.rerun()

elif st.session_state.game["view"] == "shop":
    render_shop()
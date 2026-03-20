import hashlib
import re
from datetime import datetime
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="보험 규제 뉴스/보도자료 모니터", layout="wide")

DEFAULT_KEYWORDS = ["1200%", "차익거래", "모집수수료"]
NEWS_HL = "ko"
NEWS_GL = "KR"
NEWS_CEID = "KR:ko"


def build_google_news_rss_query(keywords, days=7):
    """
    Google News RSS 검색용 쿼리 생성
    예: ("1200%" OR "차익거래" OR "모집수수료") when:7d
    """
    quoted = [f'"{kw}"' for kw in keywords if kw.strip()]
    if not quoted:
        raise ValueError("키워드가 비어 있습니다.")
    query = f"({' OR '.join(quoted)}) when:{days}d"
    url = (
        f"https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl={NEWS_HL}&gl={NEWS_GL}&ceid={NEWS_CEID}"
    )
    return url, query


def parse_google_news_rss(feed_url):
    feed = feedparser.parse(feed_url)
    items = []

    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        published = entry.get("published", "")
        source = ""

        if hasattr(entry, "source"):
            try:
                source = entry.source.get("title", "")
            except Exception:
                source = str(entry.source)

        items.append(
            {
                "type": "뉴스",
                "title": title,
                "source": source,
                "published": published,
                "url": link,
                "body_preview": "",
            }
        )
    return items


def safe_get(url, timeout=15):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; InsuranceNewsMonitor/1.0)"
    }
    return requests.get(url, headers=headers, timeout=timeout)


def parse_klia_press(max_items=20):
    """
    생명보험협회 보도자료 예시 파서
    사이트 구조 변경 가능성이 있으므로 실패 시 빈 리스트 반환
    """
    results = []
    url = "https://www.klia.or.kr/?sub_num=703"

    try:
        resp = safe_get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 일반 게시판 링크 추정 파싱
        links = soup.select("a")
        seen = set()

        for a in links:
            text = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if not text:
                continue

            if "보도자료" in text or re.search(r"모집수수료|차익거래|1200%", text):
                full_url = href
                if href.startswith("/"):
                    full_url = "https://www.klia.or.kr" + href
                if full_url in seen:
                    continue
                seen.add(full_url)

                results.append(
                    {
                        "type": "보도자료(생보협)",
                        "title": text,
                        "source": "생명보험협회",
                        "published": "",
                        "url": full_url,
                        "body_preview": "",
                    }
                )
                if len(results) >= max_items:
                    break
    except Exception:
        return []

    return results


def score_text(row, keywords):
    text = f"{row.get('title', '')} {row.get('body_preview', '')} {row.get('source', '')}".lower()
    score = 0
    matched = []

    for kw in keywords:
        if kw.lower() in text:
            score += 5
            matched.append(kw)

    # 확장 키워드
    expanded = ["선지급수수료", "유지수수료", "판매수수료", "GA", "보험대리점", "사업비"]
    for kw in expanded:
        if kw.lower() in text:
            score += 2

    if "보험" in text:
        score += 1
    if "감독규정" in text:
        score += 1

    return score, ", ".join(sorted(set(matched)))


def dedupe_items(items):
    seen = set()
    deduped = []

    for item in items:
        key = item.get("url") or hashlib.md5(item.get("title", "").encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped


def run_collection(keywords, days):
    rss_url, query = build_google_news_rss_query(keywords, days=days)
    news_items = parse_google_news_rss(rss_url)
    press_items = parse_klia_press(max_items=20)

    all_items = dedupe_items(news_items + press_items)
    rows = []

    for item in all_items:
        score, matched = score_text(item, keywords)
        item["score"] = score
        item["matched_keywords"] = matched
        rows.append(item)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(by=["score", "published"], ascending=[False, False])
    return df, rss_url, query


st.title("보험 규제 뉴스/보도자료 모니터")
st.caption("키워드 기반으로 뉴스 RSS와 일부 공식 보도자료를 수집합니다.")

with st.sidebar:
    st.header("검색 조건")
    keyword_text = st.text_area(
        "키워드 (줄바꿈 구분)",
        value="\n".join(DEFAULT_KEYWORDS),
        height=120,
    )
    days = st.selectbox("최근 기간", [1, 3, 7, 14, 30], index=2)
    run_btn = st.button("수집 실행", use_container_width=True)

keywords = [x.strip() for x in keyword_text.splitlines() if x.strip()]

if run_btn:
    if not keywords:
        st.error("키워드를 1개 이상 입력하세요.")
    else:
        with st.spinner("수집 중입니다..."):
            df, rss_url, query = run_collection(keywords, days)

        st.subheader("실행 정보")
        st.write(f"- 검색 쿼리: `{query}`")
        st.write(f"- RSS URL: {rss_url}")

        st.subheader("결과")
        if df.empty:
            st.warning("검색 결과가 없습니다.")
        else:
            show_df = df[["type", "title", "source", "published", "matched_keywords", "score", "url"]].copy()
            st.dataframe(show_df, use_container_width=True)

            csv_bytes = show_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "CSV 다운로드",
                data=csv_bytes,
                file_name=f"insurance_news_monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )

            st.subheader("상위 결과 상세")
            top_n = min(10, len(df))
            for _, row in df.head(top_n).iterrows():
                with st.expander(f"[{row['type']}] {row['title']}"):
                    st.write(f"**출처**: {row['source']}")
                    st.write(f"**발행시각**: {row['published']}")
                    st.write(f"**매칭 키워드**: {row['matched_keywords']}")
                    st.write(f"**점수**: {row['score']}")
                    st.write(f"**링크**: {row['url']}")
else:
    st.info("왼쪽에서 키워드와 기간을 설정한 뒤 '수집 실행'을 누르세요.")

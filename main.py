import streamlit as st
from scrape import scrape_website, extract_body_content, clean_body_content, split_dom_content
from parse import parse_with_ollama 
import pandas as pd
from intent import parse_prompt
from pipeline import run_search


st.title("Instagram creator finder")
prompt = st.text_area(
    "What are you looking for?",
    placeholder='e.g. "find 50 female tech creators from Pakistan over 10k followers #techtok"\n'
                'or   "find this profile mkbhd"',
)
col1, col2, col3 = st.columns(3)
use_llm = col1.checkbox("LLM analysis (needed for country/gender checks)", value=True)
strict = col2.checkbox("Strict (verified country/gender only)", value=False)
verify_country = col3.checkbox("Extra country check (slower)", value=True)

if st.button("Search") and prompt.strip():
    intent = parse_prompt(prompt, use_llm=use_llm)
    intent.strict = strict
    with st.expander("How I understood your request", expanded=True):
        st.json(intent.to_dict())

    bar = st.progress(0.0, text="Starting...")
    def on_progress(done, total, msg):
        bar.progress(min(done / max(total, 1), 1.0), text=msg)

    results, stats = run_search(intent, headless=True, use_llm=use_llm, on_progress=on_progress,
                             verify_country=verify_country)

    if results:
        df = pd.DataFrame(results).drop(columns=["profile_image"], errors="ignore")
        st.dataframe(df, use_container_width=True)
        st.download_button("Download CSV", df.to_csv(index=False), "creators.csv", "text/csv")
    else:
        st.warning("No matching profiles found.")
    st.caption(f"Stopped because: {stats['stopped']} · checked {stats['checked']} · "
               f"rejected {stats['rejected']} · unreadable {stats['unreadable']}")

st.title("AI Web Scrapper")
url = st.text_input("Enter the URL of the website you want to scrape:")


if st.button("Scrape Site"):
    st.write("Scraping the website...")
    result = scrape_website(url)
    body_content = extract_body_content(result)
    cleaned_content = clean_body_content(body_content)

    st.session_state.dom_content = cleaned_content

    with st.expander("View DOM Content"):
        st.text_area("DOM Content", cleaned_content, height=300)

if "dom_content" in st.session_state:
    parse_description = st.text_area("Describe what you want to parse?")

    if st.button("Parse Content"):
        if parse_description:
            st.write("Parsing the content...")

            dom_chunks = split_dom_content(st.session_state.dom_content)
            result = parse_with_ollama(dom_chunks, parse_description)
            st.write(result)



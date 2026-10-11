import streamlit as st
from scrape import scrape_website 

st.title("AI Web Scrapper")
url = st.text_input("Enter the URL of the website you want to scrape:")


if st.button("Scrape Site"):
    st.write("Scraping the website...")
    result = scrape_website(url)
    print(result)
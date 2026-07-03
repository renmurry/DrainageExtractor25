import streamlit as st

st.set_page_config(page_title="Drainage Network Extractor", layout="wide")
st.title("Drainage Network Extractor")
st.caption("skeleton: just checking the app runs")

with st.sidebar:
    st.header("Inputs")
    st.file_uploader("DEM GeoTIFF(s)", type=["tif", "tiff"], accept_multiple_files=True)

st.success("App is alive. We'll add real features next.")

"""Shared styles and session helpers."""
import streamlit as st


def apply_styles():
    st.markdown("""
    <style>
    .block-container { padding-top: 1rem; }
    h1 { font-size: 1.5rem !important; }
    h2 { font-size: 1.25rem !important; }
    .stAlert { font-size: 0.9rem; }
    </style>
    """, unsafe_allow_html=True)


def load_station():
    """Return the currently selected station dict from session state, or None."""
    return st.session_state.get("we_station")


def set_station(info: dict):
    st.session_state["we_station"] = info

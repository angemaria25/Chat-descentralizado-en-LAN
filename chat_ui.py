import streamlit as st
from chat import iniciar_sockets, iniciar_servicios
import threading 
import time

st.set_page_config(page_title="Chat LAN", layout="wide")

if 'inicializado' not in st.session_state:
    st.session_state.inicializado = False
    
with st.sidebar:
    st.header("🚀 Panel de Control")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("▶️ Iniciar Servicios", key="iniciar_btn"):
            iniciar_sockets()
            iniciar_servicios()
            st.session_state.inicializado = True
    
    with col2:
        if st.button("⏹ Detener Servicios", key="detener_btn"):
            st.session_state.inicializado = False
            st.toast("Servicios detenidos", icon="⚠️")

st.title("💬 Chat LAN - Protocolo LCP")
st.divider()


#Estado de conexión
status_placeholder = st.empty()
if st.session_state.inicializado:
    status_placeholder.success("✅ Estado: CONECTADO", icon="🟢")
    st.write("Aquí irá el chat activo...")
else:
    status_placeholder.warning("⚠️ Estado: DESCONECTADO", icon="🔴")
    st.info("Presiona 'Iniciar Servicios' en el panel lateral para comenzar")
    

import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import numpy as np

st.set_page_config(page_title="Bem Trading Asistan", layout="wide")
st.title("🤖 Bem Funding - Teknik Sinyal Asistanı")

# 🎛️ Kenar Çubuğu Ayarları
symbol = st.sidebar.selectbox("Sembol", ["BTC-USD", "ETH-USD", "SOL-USD"])
timeframe = st.sidebar.radio("Zaman Dilimi", ["15m", "1h", "4h"], index=1)
fvg_size = st.sidebar.slider("Min FVG Boşluğu (%)", 0.1, 2.0, 0.5)

st.sidebar.markdown("---")
st.sidebar.info("💡 Bu asistan otomatik işlem yapmaz. Sadece görsel sinyal ve seviye önerisi sunar.")

# 📊 Veri Çek
@st.cache_data(ttl=300)
def get_data(sym, tf):
    df = yf.download(sym, period="60d", interval=tf)
    df.columns = [c.lower() for c in df.columns]
    return df

df = get_data(symbol, timeframe)
if df.empty:
    st.error("Veri çekilemedi. İnternet bağlantını kontrol et.")
    st.stop()

# 🔍 Basit FVG Tespiti (3 Mum Kuralı)
def detect_fvg(data, threshold_pct):
    fvgs = []
    for i in range(2, len(data)):
        high_prev2 = data['high'].iloc[i-2]
        low_curr = data['low'].iloc[i]
        
        # Bullish FVG: Şu anki LOW > 2 mum önceki HIGH
        if low_curr > high_prev2:
            gap_pct = (low_curr - high_prev2) / high_prev2 * 100
            if gap_pct >= threshold_pct:
                fvgs.append({
                    'type': 'AL',
                    'start_idx': i-2,
                    'end_idx': i,
                    'zone': (high_prev2, low_curr),
                    'entry': (high_prev2 + low_curr) / 2
                })
    return fvgs

fvgs = detect_fvg(df, fvg_size)

# 📈 Grafik Çiz
fig = go.Figure()
fig.add_trace(go.Candlestick(
    x=df.index, open=df['open'], high=df['high'],
    low=df['low'], close=df['close'], name="Fiyat"
))

# FVG Bölgelerini Kutu Olarak Ekle
for f in fvgs:
    fig.add_shape(type="rect",
        x0=df.index[f['start_idx']], x1=df.index[f['end_idx']],
        y0=f['zone'][0], y1=f['zone'][1],
        line=dict(color="rgba(0,200,0,0.3)", width=1),
        fillcolor="rgba(0,200,0,0.2)"
    )
    fig.add_annotation(x=df.index[f['end_idx']], y=f['entry'],
                       text="🟢 FVG", showarrow=False, font=dict(color="green"))

fig.update_layout(title=f"{symbol} | {timeframe}", xaxis_rangeslider_visible=False, template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# 🎯 Sinyal Paneli
st.subheader("📊 Güncel Durum")
if fvgs:
    latest = fvgs[-1]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Sinyal", "🟢 AL Bölgesi")
    col2.metric("Giriş", f"${latest['entry']:.2f}")
    col3.metric("Stop Loss", f"${latest['zone'][0] - (latest['entry']-latest['zone'][0]):.2f}")
    col4.metric("Take Profit", f"${latest['entry'] + (latest['entry']-latest['zone'][0])*1.5:.2f}")
    st.success("✅ Fiyat FVG bölgesine yaklaştı veya içinde. Risk yönetimi ile giriş yapabilirsin.")
else:
    st.info("⏳ Şu an aktif FVG bölgesi yok. Beklemede kal veya farklı timeframe/sembol dene.")

# 📝 Journal Notu
st.markdown("---")
st.caption("⚠️ Yasal Uyarı: Bu araç eğitim ve destek amaçlıdır. Yatırım tavsiyesi değildir. Bem Funding kurallarına uymak ve risk yönetimini uygulamak tamamen kullanıcı sorumluluğundadır.")

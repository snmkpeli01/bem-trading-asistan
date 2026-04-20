import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import numpy as np
from datetime import datetime, timedelta

st.set_page_config(page_title="🤖 Werlein Forever Model - Bem Asistan", layout="wide")
st.title("🎯 Justin Werlein 'Forever Model' - Bem Trading Asistanı")

# 🎛️ Kenar Çubuğu - Werlein Parametreleri
with st.sidebar:
    st.subheader("⚙️ Strateji Ayarları")
    symbol = st.selectbox("Sembol", ["BTC-USD", "ETH-USD", "SOL-USD"], index=0)
    execution_tf = st.radio("Execution TF (LTF)", ["1m", "3m", "5m"], index=2)
    analysis_tf = st.radio("Analysis TF (HTF)", ["1h", "4h"], index=0)
    
    st.markdown("---")
    st.subheader("📏 Werlein Kuralları")
    min_rr = st.slider("Min Risk/Reward", 1.5, 4.0, 2.0, 0.1)
    fvg_threshold_pct = st.slider("Min FVG Boşluğu (%)", 0.1, 1.5, 0.3, 0.1)
    std_dev_tp = st.slider("TP Std. Sapma Çarpanı", 1.5, 3.0, 2.0, 0.1)
    
    st.markdown("---")
    st.info("📚 Werlein Kuralları:\n• Premium'da short, discount'ta long\n• Min 2R olmadan işlem yok\n• CISD retest'te giriş\n• SMT divergence kontrolü")

# 📊 Veri Çekme Fonksiyonu
@st.cache_data(ttl=300)
def get_data(sym, tf, period="60d"):
    df = yf.download(sym, period=period, interval=tf)
    if df.empty:
        return None
    df.columns = [c.lower() for c in df.columns]
    df['range'] = df['high'] - df['low']
    return df

# 🔍 FVG Tespiti (Werlein Tanımı: Non-overlapping wicks)
def detect_fvg(df, threshold_pct):
    fvgs = []
    for i in range(2, len(df)):
        # Bullish FVG: current LOW > prev2 HIGH
        if df['low'].iloc[i] > df['high'].iloc[i-2]:
            gap_pct = (df['low'].iloc[i] - df['high'].iloc[i-2]) / df['high'].iloc[i-2] * 100
            if gap_pct >= threshold_pct:
                fvgs.append({
                    'type': 'bullish',
                    'idx_start': i-2, 'idx_end': i,
                    'zone_low': df['high'].iloc[i-2],
                    'zone_high': df['low'].iloc[i],
                    'mid': (df['high'].iloc[i-2] + df['low'].iloc[i]) / 2
                })
        # Bearish FVG: current HIGH < prev2 LOW
        elif df['high'].iloc[i] < df['low'].iloc[i-2]:
            gap_pct = (df['low'].iloc[i-2] - df['high'].iloc[i]) / df['low'].iloc[i-2] * 100
            if gap_pct >= threshold_pct:
                fvgs.append({
                    'type': 'bearish',
                    'idx_start': i-2, 'idx_end': i,
                    'zone_low': df['high'].iloc[i],
                    'zone_high': df['low'].iloc[i-2],
                    'mid': (df['high'].iloc[i] + df['low'].iloc[i-2]) / 2
                })
    return fvgs

# 🔁 IFVG Tespiti (FVG ters yönde kırıldığında)
def detect_ifvg(df, fvgs):
    ifvgs = []
    for fvg in fvgs:
        idx_end = fvg['idx_end']
        if idx_end + 2 < len(df):
            if fvg['type'] == 'bullish':
                # Fiyat FVG'yi aşağı kırıp kapattı mı?
                if df['close'].iloc[idx_end+1] < fvg['zone_low']:
                    ifvgs.append({**fvg, 'ifvg_confirmed': True, 'direction': 'bearish'})
            else:
                if df['close'].iloc[idx_end+1] > fvg['zone_high']:
                    ifvgs.append({**fvg, 'ifvg_confirmed': True, 'direction': 'bullish'})
    return ifvgs

# 🔄 CISD Tespiti (Change in State of Delivery)
def detect_cisd(df, ifvgs):
    cisds = []
    for ifvg in ifvgs:
        idx = ifvg['idx_end'] + 1
        if idx + 1 < len(df):
            if ifvg['direction'] == 'bullish':
                # Long CISD: Gövde yukarı kırılım
                if df['close'].iloc[idx] > df['open'].iloc[idx-1]:
                    cisds.append({
                        'type': 'long',
                        'idx': idx,
                        'entry_zone': (ifvg['zone_high'], ifvg['zone_high'] + df['range'].iloc[idx]*0.3),
                        'sl': ifvg['zone_low'] - df['range'].iloc[idx]*0.2
                    })
            else:
                if df['close'].iloc[idx] < df['open'].iloc[idx-1]:
                    cisds.append({
                        'type': 'short',
                        'idx': idx,
                        'entry_zone': (ifvg['zone_low'] - df['range'].iloc[idx]*0.3, ifvg['zone_low']),
                        'sl': ifvg['zone_high'] + df['range'].iloc[idx]*0.2
                    })
    return cisds

# 📐 Premium/Discount Hesaplama (1H Range)
def get_premium_discount(df_htf):
    if len(df_htf) < 20:
        return None, None
    recent_high = df_htf['high'].iloc[-20:].max()
    recent_low = df_htf['low'].iloc[-20:].min()
    mid = (recent_high + recent_low) / 2
    current = df_htf['close'].iloc[-1]
    zone = 'premium' if current > mid else 'discount'
    return zone, mid

# 📈 Standart Sapma TP Hesaplama (Werlein Yöntemi)
def calculate_tp(inducement_leg, std_multiplier=2.0):
    if len(inducement_leg) < 3:
        return None
    returns = inducement_leg['close'].pct_change().dropna()
    if returns.std() == 0:
        return None
    last_price = inducement_leg['close'].iloc[-1]
    tp_distance = returns.std() * std_multiplier * last_price
    return last_price + tp_distance if returns.mean() > 0 else last_price - tp_distance

# 🎯 Ana Sinyal Üretici
def generate_werlein_signal(symbol, exec_tf, analysis_tf, fvg_thresh, min_rr, std_tp):
    # HTF Veri (1H analiz)
    df_htf = get_data(symbol, analysis_tf, period="90d")
    if df_htf is None or len(df_htf) < 50:
        return None, "HTF veri yetersiz"
    
    # LTF Veri (5m execution)
    df_ltf = get_data(symbol, exec_tf, period="7d")
    if df_ltf is None or len(df_ltf) < 100:
        return None, "LTF veri yetersiz"
    
    # 1. Premium/Discount Kontrolü (Werlein Kuralı)
    zone, mid_level = get_premium_discount(df_htf)
    if zone is None:
        return None, "Premium/discount hesaplanamadı"
    
    # 2. 1H FVG Tespiti
    fvgs_htf = detect_fvg(df_htf, fvg_thresh)
    if not fvgs_htf:
        return None, f"1H FVG bulunamadı (min %{fvg_thresh})"
    
    # 3. LTF'de IFVG + CISD Zinciri
    fvgs_ltf = detect_fvg(df_ltf, fvg_thresh)
    ifvgs = detect_ifvg(df_ltf, fvgs_ltf)
    cisds = detect_cisd(df_ltf, ifvgs)
    
    if not cisds:
        return None, "CISD onayı bekleniyor"
    
    # 4. En son CISD'yi al
    latest_cisd = cisds[-1]
    
    # 5. Risk/Ödül Hesaplama (Werlein: Min 2R)
    entry = latest_cisd['entry_zone'][1] if latest_cisd['type'] == 'long' else latest_cisd['entry_zone'][0]
    sl = latest_cisd['sl']
    risk = abs(entry - sl)
    
    # TP: Standart sapma projeksiyonu
    inducement = df_ltf.iloc[max(0, latest_cisd['idx']-10):latest_cisd['idx']+1]
    tp = calculate_tp(inducement, std_tp)
    if tp is None:
        tp = entry + (risk * min_rr) if latest_cisd['type'] == 'long' else entry - (risk * min_rr)
    
    reward = abs(tp - entry)
    rr_ratio = reward / risk if risk > 0 else 0
    
    # 6. Werlein Filtreleri
    if rr_ratio < min_rr:
        return None, f"R:R {rr_ratio:.2f} < {min_rr}R → İŞLEM YOK"
    
    # Premium/discount uyumu
    if (latest_cisd['type'] == 'long' and zone != 'discount') or \
       (latest_cisd['type'] == 'short' and zone != 'premium'):
        return None, f"{zone.upper()} bölgesinde {latest_cisd['type'].upper()} yasak (Werlein kuralı)"
    
    return {
        'direction': latest_cisd['type'].upper(),
        'entry': entry,
        'sl': sl,
        'tp': tp,
        'rr': rr_ratio,
        'zone': zone,
        'fvg_zone': (fvgs_htf[-1]['zone_low'], fvgs_htf[-1]['zone_high']) if fvgs_htf else None,
        'inducement_leg': inducement
    }, None

# 🎨 Grafik Çizim Fonksiyonu
def plot_werlein_chart(df, signal, fvgs, ifvgs, cisds):
    fig = go.Figure()
    
    # Fiyat mumları
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['open'], high=df['high'],
        low=df['low'], close=df['close'], name="Fiyat", increasing_line_color='#00C853', decreasing_line_color='#FF5252'
    ))
    
    # FVG Bölgeleri (Werlein stili)
    for f in fvgs:
        color = 'rgba(0,200,83,0.15)' if f['type']=='bullish' else 'rgba(255,82,82,0.15)'
        fig.add_shape(type="rect",
            x0=df.index[f['idx_start']], x1=df.index[f['idx_end']],
            y0=f['zone_low'], y1=f['zone_high'],
            line=dict(color=color, width=1), fillcolor=color
        )
    
    # IFVG Onayları (kesikli çizgi)
    for ifvg in ifvgs:
        fig.add_shape(type="line",
            x0=df.index[ifvg['idx_end']], x1=df.index[ifvg['idx_end']+2],
            y0=ifvg['zone_low'] if ifvg['direction']=='bullish' else ifvg['zone_high'],
            y1=ifvg['zone_low'] if ifvg['direction']=='bullish' else ifvg['zone_high'],
            line=dict(color='orange', width=2, dash='dash')
        )
    
    # CISD Giriş Noktaları
    for c in cisds:
        x_pos = df.index[c['idx']]
        y_pos = c['entry_zone'][1] if c['type']=='long' else c['entry_zone'][0]
        color = 'green' if c['type']=='long' else 'red'
        fig.add_annotation(x=x_pos, y=y_pos, text=f"⚡ {c['type'].upper()}", 
                          showarrow=True, arrowhead=2, arrowsize=1, arrowwidth=2, arrowcolor=color)
    
    # Sinyal varsa SL/TP çizgileri
    if signal:
        last_idx = len(df)-1
        last_x = df.index[last_idx]
        fig.add_hline(y=signal['entry'], line_dash="dot", line_color="yellow", annotation_text="🎯 Entry")
        fig.add_hline(y=signal['sl'], line_dash="dash", line_color="red", annotation_text="🛑 SL")
        fig.add_hline(y=signal['tp'], line_dash="dash", line_color="green", annotation_text="✅ TP")
    
    fig.update_layout(
        title=f"{symbol} | {execution_tf} Execution | {analysis_tf} Analysis",
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        height=600,
        hovermode='x unified'
    )
    return fig

# 🚀 Ana Uygulama
if __name__ == "__main__":
    st.markdown("### 📊 Canlı Analiz Paneli")
    
    # Veri ve sinyal üret
    with st.spinner("🔍 Werlein kurallarına göre tarama yapılıyor..."):
        signal, error = generate_werlein_signal(symbol, execution_tf, analysis_tf, fvg_threshold_pct, min_rr, std_dev_tp)
    
    # Hata mesajı
    if error:
        st.warning(f"⚠️ {error}")
    
    # Grafik
    df_ltf = get_data(symbol, execution_tf, period="7d")
    if df_ltf is not None:
        fvgs = detect_fvg(df_ltf, fvg_threshold_pct)
        ifvgs = detect_ifvg(df_ltf, fvgs)
        cisds = detect_cisd(df_ltf, ifvgs)
        fig = plot_werlein_chart(df_ltf, signal, fvgs, ifvgs, cisds)
        st.plotly_chart(fig, use_container_width=True)
    
    # 🎯 Sinyal Kartı (Werlein Checklist Formatında)
    if signal:
        st.success(f"🟢 SİNYAL: {signal['direction']} - Werlein Kuralları Uygun!")
        
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("🎯 Entry", f"${signal['entry']:.2f}")
        col2.metric("🛑 Stop Loss", f"${signal['sl']:.2f}", delta_color="inverse")
        col3.metric("✅ Take Profit", f"${signal['tp']:.2f}")
        col4.metric("⚖️ R:R Oranı", f"{signal['rr']:.2f}R", delta=f"{signal['rr']-min_rr:.2f}")
        col5.metric("📍 Bölge", signal['zone'].upper(), delta="Premium" if signal['zone']=='premium' else "Discount")
        
        # Werlein Checklist
        with st.expander("📋 Werlein Trade Checklist - Onaylar"):
            checks = [
                ("✅ 1H FVG tespit edildi", True),
                (f"✅ {'Premium' if signal['zone']=='premium' else 'Discount'} bölgesi", True),
                (f"✅ IFVG + CISD onayı alındı", True),
                (f"✅ Min {min_rr}R sağlandı: {signal['rr']:.2f}R", signal['rr'] >= min_rr),
                ("✅ V-Shape momentum kontrolü", "Grafikte manuel kontrol önerilir"),
                ("⚠️ Kırmızı haber kontrolü", "Economic calendar'dan teyit edin")
            ]
            for text, status in checks:
                if isinstance(status, bool):
                    st.markdown(f"{'🟢' if status else '🔴'} {text}")
                else:
                    st.markdown(f"🟡 {text} → {status}")
        
        # Bem cTrader için hızlı kopyala
        st.markdown("### 📋 Bem cTrader İçin Hızlı Kopyala")
        st.code(f"""# {signal['direction']} İşlemi - {symbol}
Entry: {signal['entry']:.2f}
SL: {signal['sl']:.2f}  # Risk: ${abs(signal['entry']-signal['sl']):.2f}
TP: {signal['tp']:.2f}  # Reward: ${abs(signal['tp']-signal['entry']):.2f}
R:R: {signal['rr']:.2f}
Pozisyon: Hesabınızın %{1} risk alacak şekilde hesaplayın""", language="bash")
    
    else:
        st.info("⏳ Beklemede: Werlein kurallarına uygun sinyal yok. Grafikte FVG/IFVG/CISD zincirini takip edin.")
    
    # 📚 Eğitim Notu
    with st.expander("📚 Justin Werlein Metodolojisi - Kısa Özet"):
        st.markdown("""
        **Forever Model Giriş Mantığı**:
        1. HTF'de (1H) DOL + FVG belirle
        2. LTF'de (5m) fiyat FVG'ye çekilsin + inducement olsun
        3. IFVG oluşumu bekle (FVG ters kırılım)
        4. CISD onayı al (gövde kapanışı)
        5. CISD retest'te giriş yap
        6. SL: Son swing dışı, TP: 2-2.5 std sapma veya 2R+
        
        **Yasaklar**:
        ❌ Kırmızı haber öncesi işlem
        ❌ Premium'da long / Discount'ta short
        ❌ 2R'den düşük R:R oranı
        ❌ SMT divergence çelişkisi varken ısrar
        
        **Bonus Güven Sinyalleri**:
        ✅ V-shape momentum formasyonu
        ✅ SMT divergence onayı (BTC/ETH korelasyon)
        ✅ Volume artışı ile teyit
        """)
    
    st.markdown("---")
    st.caption("⚠️ Yasal Uyarı: Bu araç eğitim amaçlıdır. Justin Werlein'in metodolojisini uygular ancak yatırım tavsiyesi değildir. Bem Funding kuralları ve risk yönetimi kullanıcı sorumluluğundadır.")

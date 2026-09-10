import asyncio
import datetime
import json
import flet as ft

ACCENT = "#FF5C5C"
GREEN = "#3DDC84"
BLUE = "#4AA8FF"
BG = "#0F1115"
CARD = "#1A1D27"
MUTED = "#9AA0B4"
GOLD = "#FFBD2E"

MODES = {
    "focus": {"title": "Фокус", "color": ACCENT, "emoji": "🍅"},
    "short": {"title": "Короткий отдых", "color": GREEN, "emoji": "☕"},
    "long": {"title": "Большой перерыв", "color": BLUE, "emoji": "🌴"},
}

class State:
    def __init__(self):
        self.mode = "focus"
        self.remaining = 25 * 60
        self.total = 25 * 60
        self.running = False
        self.in_set = 0
        self.total_done = 0
        self.focus_minutes = 0
        self.focus = 25
        self.short = 5
        self.long = 15
        self.per_set = 4
        self.auto_break = True
        self.auto_focus = True
        self.sound = True

def fmt(s):
    s = max(0, int(s))
    return f"{s // 60:02d}:{s % 60:02d}"

def main(page: ft.Page):
    page.title = "Pomidor"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.padding = 0
    try:
        page.window_width = 420
        page.window_height = 800
    except:
        pass
    st = State()
    prefs = page.shared_preferences
    hf = ft.HapticFeedback()
    page.overlay.append(hf)
    st.remaining = st.focus * 60
    st.total = st.remaining

    def save():
        try:
            page.run_task(prefs.set, "pomidor_cfg", json.dumps({
                "focus": st.focus, "short": st.short, "long": st.long,
                "per_set": st.per_set, "auto_break": st.auto_break,
                "auto_focus": st.auto_focus, "sound": st.sound,
                "in_set": st.in_set, "total_done": st.total_done,
                "focus_minutes": st.focus_minutes,
                "stat_date": datetime.date.today().isoformat(),
            }))
        except:
            pass

    title_time = ft.Text(fmt(st.remaining), size=64, weight=ft.FontWeight.BOLD, color="white", text_align=ft.TextAlign.CENTER)
    mode_label = ft.Text("ФОКУС", size=14, weight=ft.FontWeight.BOLD, color=ACCENT, text_align=ft.TextAlign.CENTER)
    set_label = ft.Text("", size=12, color=MUTED, text_align=ft.TextAlign.CENTER)
    dots_label = ft.Text("", size=22, text_align=ft.TextAlign.CENTER)
    fire_label = ft.Text("🔥 0", size=14, weight=ft.FontWeight.BOLD, color=GOLD)
    stat_label = ft.Text("", size=12, color=MUTED)
    status_label = ft.Text("Жми старт • 4 помидора → лонг", size=12, color=MUTED, text_align=ft.TextAlign.CENTER)
    ring = ft.ProgressRing(width=210, height=210, stroke_width=12, color=ACCENT, value=0)
    main_btn = ft.ElevatedButton("▶   СТАРТ", bgcolor=ACCENT, color="white", height=56)
    reset_btn = ft.OutlinedButton("↺  Сброс", height=48, expand=True)
    skip_btn = ft.OutlinedButton("⏭  Пропустить", height=48, expand=True)

    tab_row = ft.Row(spacing=6)
    tab_btns = {}
    for key in ("focus", "short", "long"):
        b = ft.OutlinedButton({"focus": "Фокус", "short": "Отдых", "long": "Лонг"}[key], expand=True, height=44)
        tab_btns[key] = b
        tab_row.controls.append(b)

    field_refs = {}

    def num_field(key, label, initial, lo, hi, on_change):
        tf = ft.TextField(label=label, value=str(initial), width=110, text_align=ft.TextAlign.CENTER, keyboard_type=ft.KeyboardType.NUMBER)
        field_refs[key] = tf
        def minus(e):
            try:
                v = max(lo, min(hi, int(tf.value or initial) - 1))
            except:
                v = initial
            tf.value = str(v)
            on_change(v)
            tf.update()
        def plus(e):
            try:
                v = max(lo, min(hi, int(tf.value or initial) + 1))
            except:
                v = initial
            tf.value = str(v)
            on_change(v)
            tf.update()
        def changed(e):
            try:
                v = max(lo, min(hi, int(tf.value)))
            except:
                v = initial
                tf.value = str(v)
            on_change(v)
            try:
                tf.update()
            except:
                pass
        tf.on_change = changed
        row = ft.Row([ft.IconButton(ft.Icons.REMOVE, on_click=minus), tf, ft.IconButton(ft.Icons.ADD, on_click=plus)], alignment=ft.MainAxisAlignment.CENTER, spacing=0)
        return ft.Container(ft.Column([row], alignment=ft.MainAxisAlignment.CENTER), bgcolor=CARD, border_radius=14, padding=8, expand=True)

    def after_settings():
        save()
        if not st.running:
            d = {"focus": st.focus, "short": st.short, "long": st.long}[st.mode] * 60
            st.total = d
            st.remaining = d
        refresh()

    sw_break = ft.Switch(value=st.auto_break)
    sw_focus = ft.Switch(value=st.auto_focus)
    sw_sound = ft.Switch(value=st.sound)

    def on_sw_break(e):
        st.auto_break = bool(sw_break.value)
        save()
    def on_sw_focus(e):
        st.auto_focus = bool(sw_focus.value)
        save()
    def on_sw_sound(e):
        st.sound = bool(sw_sound.value)
        save()
    sw_break.on_change = on_sw_break
    sw_focus.on_change = on_sw_focus
    sw_sound.on_change = on_sw_sound

    overlay = ft.Container(visible=False, bgcolor="#B71C1C", expand=True, opacity=1.0)
    overlay_emoji = ft.Text("🍅", size=110, text_align=ft.TextAlign.CENTER)
    overlay_title = ft.Text("", size=34, weight=ft.FontWeight.BOLD, color="white", text_align=ft.TextAlign.CENTER)
    overlay_sub = ft.Text("", size=16, color="white", text_align=ft.TextAlign.CENTER)
    overlay_state = ft.Text("", size=14, weight=ft.FontWeight.BOLD, color="#FFE082", text_align=ft.TextAlign.CENTER)
    overlay_btn = ft.ElevatedButton("ПОНЯТНО, ПРОДОЛЖИТЬ  →", height=58)
    overlay_col = ft.Column([overlay_emoji, overlay_title, overlay_sub, overlay_state, ft.Container(height=10), overlay_btn], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8, expand=True)
    overlay.content = ft.Container(overlay_col, padding=24, alignment=ft.Alignment(0, 0))

    def hide_overlay(e=None):
        overlay.visible = False
        page.update()

    overlay_btn.on_click = hide_overlay

    def show_overlay(title, sub, emoji, bgcolor, state_text):
        overlay.bgcolor = "#1A" + bgcolor[1:]
        overlay_emoji.value = emoji
        overlay_title.value = title
        overlay_sub.value = sub
        overlay_state.value = state_text
        overlay.visible = True
        page.update()
        try:
            page.run_task(hf.vibrate)
        except:
            pass

    def buzz(kind):
        if not st.sound:
            return
        try:
            page.run_task(hf.vibrate)
        except:
            pass

    def refresh():
        meta = MODES[st.mode]
        title_time.value = fmt(st.remaining)
        mode_label.value = meta["title"].upper()
        mode_label.color = meta["color"]
        ring.color = meta["color"]
        ring.value = max(0.0, min(1.0, 1 - (st.remaining / st.total))) if st.total else 0
        per = max(2, st.per_set)
        if st.mode == "long":
            dots_label.value = " ".join(["●"] * per)
        else:
            shown = st.in_set % per
            dots_label.value = " ".join(["●" if i < shown else "○" for i in range(per)])
        dots_label.color = meta["color"]
        cur = (st.in_set % per) + 1 if st.mode == "focus" else (st.in_set % per or per)
        set_label.value = f"СЕТ {min(st.in_set + 1, per) if st.mode == 'focus' else (st.in_set or per)}/{per}  •  ПОМИДОР {cur}"
        fire_label.value = f"🔥 {st.total_done}"
        stat_label.value = f"Сегодня: {st.total_done} 🍅   •   Фокуса: {st.focus_minutes} мин"
        main_btn.content = "⏸   ПАУЗА" if st.running else "▶   СТАРТ"
        try:
            page.title = f"{fmt(st.remaining)} • {meta['title']} | Pomidor"
        except:
            pass
        for k, b in tab_btns.items():
            try:
                b.style = ft.ButtonStyle(bgcolor=CARD if k != st.mode else ACCENT, color="white" if k == st.mode else MUTED)
            except:
                pass

    def do_finish(silent=False):
        per = max(2, st.per_set)
        if st.mode == "focus":
            st.total_done += 1
            st.in_set += 1
            st.focus_minutes += st.focus
            is_long = (st.in_set % per == 0)
            nxt = "long" if is_long else "short"
            title = "ПОМИДОР ГОТОВ! 🍅"
            sub = f"Сделано {st.in_set} в сете. {'Большой перерыв!' if is_long else 'Маленький отдых.'}"
            bgc = "#0D47A1" if is_long else "#B71C1C"
            emoji = "🍅"
        elif st.mode == "short":
            nxt = "focus"
            title = "ОТДЫХ КОНЧИЛСЯ! 💪"
            sub = "Пора за работу. Погнали!"
            bgc = "#1B7A43"
            emoji = "💪"
        else:
            st.in_set = 0
            nxt = "focus"
            title = "ЛОНГ КОНЧИЛСЯ! 🚀"
            sub = "Ты отдохнул. Новый сет!"
            bgc = "#1B7A43"
            emoji = "🚀"
        st.mode = nxt
        st.total = {"focus": st.focus, "short": st.short, "long": st.long}[nxt] * 60
        st.remaining = st.total
        st.running = False
        auto = (nxt in ("short", "long") and st.auto_break) or (nxt == "focus" and st.auto_focus)
        refresh()
        if auto:
            st.running = True
            status_label.value = f"Автостарт: {MODES[nxt]['title']} уже идёт ▶"
        else:
            status_label.value = f"Далее: {MODES[nxt]['title']}. Жми старт."
        refresh()
        save()
        if not silent:
            buzz(nxt)
            nd = {"focus": st.focus, "short": st.short, "long": st.long}[nxt]
            state_t = f"Далее: {MODES[nxt]['title']} {nd} мин — уже запущен ▶" if auto else f"Далее: {MODES[nxt]['title']} {nd} мин — на паузе"
            show_overlay(title, sub, emoji, bgc, state_t)
        page.update()

    async def loop():
        while True:
            await asyncio.sleep(1)
            if st.running:
                st.remaining -= 1
                if st.remaining <= 0:
                    do_finish()
                refresh()
                try:
                    page.update()
                except:
                    pass

    def toggle(e=None):
        st.running = not st.running
        if st.running:
            d = {"focus": st.focus, "short": st.short, "long": st.long}[st.mode] * 60
            if st.remaining <= 0:
                st.remaining = d
                st.total = d
            status_label.value = "Таймер идёт… не отвлекайся 🍅"
        else:
            status_label.value = "Пауза. Продолжай, когда будешь готов."
        refresh()
        page.update()

    def reset_timer(e=None):
        st.running = False
        d = {"focus": st.focus, "short": st.short, "long": st.long}[st.mode] * 60
        st.total = d
        st.remaining = d
        status_label.value = "Таймер сброшен."
        refresh()
        page.update()

    def skip(e=None):
        do_finish(silent=True)

    def switch_mode(mode):
        st.running = False
        st.mode = mode
        d = {"focus": st.focus, "short": st.short, "long": st.long}[mode] * 60
        st.total = d
        st.remaining = d
        status_label.value = f"Режим: {MODES[mode]['title']}. Жми старт."
        refresh()
        page.update()

    main_btn.on_click = toggle
    reset_btn.on_click = reset_timer
    skip_btn.on_click = skip
    for k, b in tab_btns.items():
        b.on_click = lambda e, m=k: switch_mode(m)

    def set_focus(v):
        st.focus = int(v)
        after_settings()
        page.update()
    def set_short(v):
        st.short = int(v)
        after_settings()
        page.update()
    def set_long(v):
        st.long = int(v)
        after_settings()
        page.update()
    def set_per(v):
        st.per_set = int(v)
        after_settings()
        page.update()

    settings_grid = ft.Column([
        ft.Row([num_field("focus", "Фокус", st.focus, 1, 120, set_focus), num_field("short", "Отдых", st.short, 1, 60, set_short)], spacing=8),
        ft.Row([num_field("long", "Лонг", st.long, 5, 90, set_long), num_field("per_set", "До лонга", st.per_set, 2, 8, set_per)], spacing=8),
        ft.Row([ft.Text("Автостарт отдыха", expand=True), sw_break]),
        ft.Row([ft.Text("Автостарт фокуса", expand=True), sw_focus]),
        ft.Row([ft.Text("Вибрация/звук", expand=True), sw_sound]),
    ], spacing=8)

    body = ft.Column([
        ft.Container(ft.Row([ft.Text("🍅  POMIDOR", size=18, weight=ft.FontWeight.BOLD), fire_label], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), padding=ft.Padding.only(left=20, right=20, top=16)),
        ft.Container(ft.Text("4 помидора → большой перерыв • отдых и фокус стартуют сами", size=11, color=MUTED), padding=ft.Padding.only(left=20, right=20)),
        ft.Container(tab_row, padding=ft.Padding.only(left=16, right=16, top=10)),
        ft.Container(ft.Column([set_label, mode_label, ft.Stack([ft.Container(ring, alignment=ft.Alignment(0, 0), padding=10), ft.Container(title_time, alignment=ft.Alignment(0, 0), padding=ft.Padding.only(top=52))], height=230), dots_label], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER), bgcolor=CARD, border_radius=20, padding=14, margin=ft.Margin.only(left=16, right=16, top=10)),
        ft.Container(main_btn, padding=ft.Padding.only(left=16, right=16, top=10)),
        ft.Container(ft.Row([reset_btn, skip_btn], spacing=8), padding=ft.Padding.only(left=16, right=16)),
        ft.Container(ft.Column([ft.Text("НАСТРОЙКИ ВРЕМЕНИ", size=11, weight=ft.FontWeight.BOLD, color=MUTED), settings_grid], spacing=8), bgcolor=CARD, border_radius=20, padding=14, margin=ft.Margin.only(left=16, right=16, top=10)),
        ft.Container(ft.Column([stat_label, status_label], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4), padding=ft.Padding.only(left=20, right=20, top=10, bottom=30)),
    ], scroll=ft.ScrollMode.AUTO, expand=True)

    async def load_prefs():
        try:
            raw = await prefs.get("pomidor_cfg")
            if not isinstance(raw, str):
                return
            d = json.loads(raw)
            for k in ("focus", "short", "long", "per_set"):
                if k in d:
                    try:
                        setattr(st, k, max(1, int(d[k])))
                    except:
                        pass
            for k in ("auto_break", "auto_focus", "sound"):
                if k in d:
                    setattr(st, k, bool(d[k]))
            for k in ("focus", "short", "long", "per_set"):
                try:
                    field_refs[k].value = str(getattr(st, k))
                except:
                    pass
            try:
                st.in_set = max(0, int(d.get("in_set", 0)))
            except:
                pass
            if d.get("stat_date") == datetime.date.today().isoformat():
                for k in ("total_done", "focus_minutes"):
                    try:
                        setattr(st, k, max(0, int(d.get(k, 0))))
                    except:
                        pass
            sw_break.value = st.auto_break
            sw_focus.value = st.auto_focus
            sw_sound.value = st.sound
            if not st.running:
                st.total = {"focus": st.focus, "short": st.short, "long": st.long}[st.mode] * 60
                st.remaining = st.total
            refresh()
            page.update()
        except:
            pass

    page.add(ft.Stack([body, overlay], expand=True))
    refresh()
    page.update()
    page.run_task(loop)
    page.run_task(load_prefs)

if __name__ == "__main__":
    ft.app(main)

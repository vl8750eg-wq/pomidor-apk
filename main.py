import asyncio
import datetime
import json
import os
import time
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

# Каталог данных приложения (на Android задаётся рантаймом Flet).
DATA_DIR = os.environ.get("FLET_APP_STORAGE_DATA") or "."
ACK_FILE = os.path.join(DATA_DIR, "pomidor_ack.json")
STATE_FILE = os.path.join(DATA_DIR, "pomidor_state.json")


class State:
    def __init__(self):
        self.mode = "focus"
        self.left = 25 * 60          # остаток фазы в секундах (когда таймер стоит)
        self.total = 25 * 60         # длительность текущей фазы
        self.ends_at = 0.0           # дедлайн по настенным часам (когда таймер идёт)
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
        self.vibro = True


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
    page.services.append(hf)  # в новом API HapticFeedback — Service, а не визуальный контрол
    st.left = st.focus * 60
    st.total = st.left

    def dur(mode=None):
        return {"focus": st.focus, "short": st.short, "long": st.long}[mode or st.mode] * 60

    # ------------------------------------------------------------------
    # Нативный мост v1.0.4 (Kotlin-центричный): Python пишет ПОЛНЫЙ снимок
    # состояния таймера в pomidor_state.json. MainActivity на Android сам
    # опрашивает этот файл каждые 0.7 с, пока приложение на экране, и ставит
    # ТОЧНЫЙ системный будильник (AlarmManager.setAlarmClock). В дедлайн
    # система запускает AlarmActivity ПОВЕРХ ВСЕХ ОКОН и лоскрина — со звуком
    # и вибрацией, даже если уведомления запрещены и процесс убит. Автофазы
    # (отдых/фокус) заранее считаются в поле "next": Kotlin перевзводит
    # будильник сам, без Python. Статус службы — в pomidor_ack.json.
    # ------------------------------------------------------------------
    _gen = [str(time.time_ns())]
    _last_state_write = [0.0]

    def _next_gen():
        _gen[0] = str(time.time_ns())
        return _gen[0]

    def mode_alarm_title(mode):
        m = MODES[mode]
        return f"{m['emoji']} {m['title'].upper()} — ВРЕМЯ ВЫШЛО!"

    def next_phase_payload():
        """Фаза, которая автостартует сразу после текущей (цепочка Kotlin)."""
        per = max(2, st.per_set)
        if st.mode == "focus":
            is_long = (st.in_set + 1) % per == 0
            nxt = "long" if is_long else "short"
        else:
            nxt = "focus"
        auto = (nxt in ("short", "long") and st.auto_break) or (nxt == "focus" and st.auto_focus)
        if not auto:
            return {"active": False}
        m = MODES[nxt]
        end_clock = datetime.datetime.fromtimestamp(st.ends_at + dur(nxt)).strftime("%H:%M")
        return {
            "active": True,
            "ts_ms": int((st.ends_at + dur(nxt)) * 1000),
            "title": mode_alarm_title(nxt),
            "body": f"Фаза «{m['title']}» завершится в {end_clock}",
            "sound": bool(st.sound),
            "vibro": bool(st.vibro),
        }

    def write_state(active=None, act=None, act_until=0):
        """Снимок состояния для нативной службы: Kotlin сам решает, ставить ли
        будильник, сверяясь с полем gen (каждая запись уникальна)."""
        try:
            run = bool(st.running) if active is None else bool(active)
            p = {"gen": _next_gen()}
            if run and st.ends_at > 0:
                end_clock = datetime.datetime.fromtimestamp(st.ends_at).strftime("%H:%M")
                p.update({
                    "active": True,
                    "ts_ms": int(st.ends_at * 1000),
                    "title": mode_alarm_title(st.mode),
                    "body": f"Таймер «{MODES[st.mode]['title']}» завершился в {end_clock}",
                    "sound": bool(st.sound),
                    "vibro": bool(st.vibro),
                    "next": next_phase_payload(),
                })
            else:
                p.update({"active": False, "ts_ms": 0, "next": {"active": False}})
            if act:
                p["act"] = act
                if act_until:
                    p["act_until"] = int(act_until)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(p, f, ensure_ascii=False)
            _last_state_write[0] = time.time()
        except Exception:
            pass

    def schedule_alarm():
        if st.running and st.ends_at > 0:
            write_state()

    def cancel_alarm():
        write_state(active=False)

    # ------------------------------------------------------------------
    # Конфиг
    # ------------------------------------------------------------------
    def save():
        try:
            page.run_task(prefs.set, "pomidor_cfg", json.dumps({
                "focus": st.focus, "short": st.short, "long": st.long,
                "per_set": st.per_set, "auto_break": st.auto_break,
                "auto_focus": st.auto_focus, "sound": st.sound,
                "vibro": st.vibro,
                "in_set": st.in_set, "total_done": st.total_done,
                "focus_minutes": st.focus_minutes,
                "stat_date": datetime.date.today().isoformat(),
                # состояние таймера — чтобы пережить убийство процесса
                "run_active": bool(st.running),
                "run_mode": st.mode if st.running else "",
                "run_ends_at": st.ends_at if st.running else 0.0,
                "run_left": (st.ends_at - time.time()) if st.running else st.left,
                "run_total": st.total,
            }))
        except:
            pass

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    title_time = ft.Text(fmt(st.left), size=64, weight=ft.FontWeight.BOLD, color="white", text_align=ft.TextAlign.CENTER)
    mode_label = ft.Text("ФОКУС", size=14, weight=ft.FontWeight.BOLD, color=ACCENT, text_align=ft.TextAlign.CENTER)
    set_label = ft.Text("", size=12, color=MUTED, text_align=ft.TextAlign.CENTER)
    dots_label = ft.Text("", size=22, text_align=ft.TextAlign.CENTER)
    fire_label = ft.Text("🔥 0", size=14, weight=ft.FontWeight.BOLD, color=GOLD)
    stat_label = ft.Text("", size=12, color=MUTED)
    status_label = ft.Text("Жми старт • 4 помидора → лонг", size=12, color=MUTED, text_align=ft.TextAlign.CENTER)
    ring = ft.ProgressRing(width=210, height=210, stroke_width=12, color=ACCENT, value=0)
    main_btn = ft.Button("▶   СТАРТ", bgcolor=ACCENT, color="white", height=56)
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
            st.total = dur()
            st.left = st.total
        refresh()

    sw_break = ft.Switch(value=st.auto_break)
    sw_focus = ft.Switch(value=st.auto_focus)
    sw_sound = ft.Switch(value=st.sound)
    sw_vibro = ft.Switch(value=st.vibro)

    def on_sw_break(e):
        st.auto_break = bool(sw_break.value)
        save()
    def on_sw_focus(e):
        st.auto_focus = bool(sw_focus.value)
        save()
    def on_sw_sound(e):
        st.sound = bool(sw_sound.value)
        if st.running:
            schedule_alarm()  # перезаписать флаг звука уже поставленного будильника
        save()
    def on_sw_vibro(e):
        st.vibro = bool(sw_vibro.value)
        if st.running:
            schedule_alarm()
        save()
    sw_break.on_change = on_sw_break
    sw_focus.on_change = on_sw_focus
    sw_sound.on_change = on_sw_sound
    sw_vibro.on_change = on_sw_vibro

    notif_label = ft.Text("", size=11, color=MUTED)

    # Баннер состояния службы будильника (виден только при проблемах).
    banner = ft.Container(visible=False, bgcolor="#7A1B1B", border_radius=12, padding=12, margin=ft.Margin.only(left=16, right=16, top=8))
    banner_text = ft.Text("", size=12, color="white", text_align=ft.TextAlign.CENTER)
    banner_state = {"kind": None}

    def open_notif_settings(e=None):
        write_state(act="settings")

    def run_test(e=None):
        write_state(act="test")
        notif_label.value = "🔔 Тест поставлен: сверни приложение — через ~15 с сработает будильник."
        notif_label.color = GOLD
        try:
            notif_label.update()
        except Exception:
            page.update()

    banner.content = ft.Column([
        banner_text,
        ft.Row([
            ft.Button("Настройки уведомлений", height=38, expand=True, on_click=open_notif_settings),
            ft.Button("Тест 15 с", height=38, expand=True, on_click=run_test),
        ], spacing=8),
    ], spacing=6)

    def set_banner(kind, text):
        banner_state["kind"] = kind
        if kind is None:
            banner.visible = False
        else:
            banner.bgcolor = "#7A1B1B" if kind == "perm" else "#7A621B"
            banner_text.value = text
            banner.visible = True
        try:
            page.update()
        except Exception:
            pass

    overlay = ft.Container(visible=False, bgcolor="#B71C1C", opacity=1.0, left=0, top=0, right=0, bottom=0)
    overlay_emoji = ft.Text("🍅", size=110, text_align=ft.TextAlign.CENTER)
    overlay_title = ft.Text("", size=34, weight=ft.FontWeight.BOLD, color="white", text_align=ft.TextAlign.CENTER)
    overlay_sub = ft.Text("", size=16, color="white", text_align=ft.TextAlign.CENTER)
    overlay_state = ft.Text("", size=14, weight=ft.FontWeight.BOLD, color="#FFE082", text_align=ft.TextAlign.CENTER)
    overlay_btn = ft.Button("ПОНЯТНО, ПРОДОЛЖИТЬ  →", height=58)
    overlay_col = ft.Column([overlay_emoji, overlay_title, overlay_sub, overlay_state, ft.Container(height=10), overlay_btn], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8, expand=True)
    overlay.content = ft.Container(overlay_col, padding=24, alignment=ft.Alignment(0, 0))

    def hide_overlay(e=None):
        overlay.visible = False
        write_state(act="stop")  # глушит нативный звук будильника
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

    def buzz(kind=None):
        if not st.vibro:
            return
        async def _v():
            for _ in range(3):
                try:
                    await hf.vibrate()
                except Exception:
                    return
                await asyncio.sleep(0.8)
        try:
            page.run_task(_v)
        except Exception:
            pass

    def refresh():
        meta = MODES[st.mode]
        title_time.value = fmt(st.left)
        mode_label.value = meta["title"].upper()
        mode_label.color = meta["color"]
        ring.color = meta["color"]
        ring.value = max(0.0, min(1.0, 1 - (st.left / st.total))) if st.total else 0
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
            page.title = f"{fmt(st.left)} • {meta['title']} | Pomidor"
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
        st.total = dur(nxt)
        st.left = st.total
        st.running = False
        st.ends_at = 0.0
        auto = (nxt in ("short", "long") and st.auto_break) or (nxt == "focus" and st.auto_focus)
        if auto:
            st.running = True
            st.ends_at = time.time() + st.left
        refresh()
        if auto:
            status_label.value = f"Автостарт: {MODES[nxt]['title']} уже идёт ▶"
        else:
            status_label.value = f"Далее: {MODES[nxt]['title']}. Жми старт."
        refresh()
        save()
        if not silent and st.sound:
            # один снимок: будильник следующей фазы + команда «звук сейчас»
            write_state(act="play", act_until=int((time.time() + 90) * 1000))
        else:
            write_state()
        if not silent:
            buzz(nxt)
            nd = dur(nxt) // 60
            state_t = f"Далее: {MODES[nxt]['title']} {nd} мин — уже запущен ▶" if auto else f"Далее: {MODES[nxt]['title']} {nd} мин — на паузе"
            show_overlay(title, sub, emoji, bgc, state_t)
        page.update()

    async def loop():
        last_sec = -1
        while True:
            await asyncio.sleep(0.2)
            if not st.running or st.ends_at <= 0:
                continue
            st.left = st.ends_at - time.time()
            if st.left <= 0:
                st.left = 0
                do_finish()
                last_sec = -1
                continue
            s = int(st.left)
            if s != last_sec:
                last_sec = s
                refresh()
                try:
                    page.update()
                except:
                    pass

    def toggle(e=None):
        if not st.running:
            if st.left <= 0:
                st.left = dur()
                st.total = st.left
            st.ends_at = time.time() + st.left
            st.running = True
            status_label.value = "Таймер идёт… не отвлекайся 🍅"
            schedule_alarm()
        else:
            st.left = max(0.0, st.ends_at - time.time())
            st.running = False
            st.ends_at = 0.0
            status_label.value = "Пауза. Продолжай, когда будешь готов."
            cancel_alarm()
        refresh()
        page.update()

    def reset_timer(e=None):
        st.running = False
        st.ends_at = 0.0
        st.total = dur()
        st.left = st.total
        status_label.value = "Таймер сброшен."
        cancel_alarm()
        refresh()
        page.update()

    def skip(e=None):
        do_finish(silent=True)

    def switch_mode(mode):
        st.running = False
        st.ends_at = 0.0
        st.mode = mode
        st.total = dur(mode)
        st.left = st.total
        status_label.value = f"Режим: {MODES[mode]['title']}. Жми старт."
        cancel_alarm()
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
        ft.Row([ft.Text("🔊 Звук будильника", expand=True), sw_sound]),
        ft.Row([ft.Text("📳 Вибрация", expand=True), sw_vibro]),
        ft.Row([ft.Button("🔔 Тест будильника (15 с)", height=44, expand=True, on_click=run_test)]),
        ft.Container(notif_label, padding=ft.Padding.only(top=2)),
    ], spacing=8)

    body = ft.Column([
        ft.Container(ft.Row([ft.Text("🍅  POMIDOR", size=18, weight=ft.FontWeight.BOLD), fire_label], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), padding=ft.Padding.only(left=20, right=20, top=16)),
        ft.Container(ft.Text("4 помидора → большой перерыв • будильник сработает даже в фоне", size=11, color=MUTED), padding=ft.Padding.only(left=20, right=20)),
        banner,
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
                write_state()  # первый запуск: пустой снимок → Kotlin ack со статусом разрешений
                return
            d = json.loads(raw)
            for k in ("focus", "short", "long", "per_set"):
                if k in d:
                    try:
                        setattr(st, k, max(1, int(d[k])))
                    except:
                        pass
            for k in ("auto_break", "auto_focus", "sound", "vibro"):
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
            sw_vibro.value = st.vibro

            # --- восстановление таймера после убийства процесса ---
            ra = bool(d.get("run_active"))
            rm = d.get("run_mode") or ""
            try:
                re_at = float(d.get("run_ends_at") or 0.0)
            except:
                re_at = 0.0
            try:
                rl = float(d.get("run_left") or 0.0)
            except:
                rl = 0.0
            try:
                rt = float(d.get("run_total") or 0.0)
            except:
                rt = 0.0
            if ra and rm in MODES:
                st.mode = rm
                st.total = rt if rt > 0 else dur(rm)
                now = time.time()
                if re_at > now:
                    # таймер ещё не дошёл до конца — продолжаем и перевзводим будильник
                    st.running = True
                    st.ends_at = re_at
                    st.left = re_at - now
                    status_label.value = "Таймер продолжается после перезапуска ▶"
                    schedule_alarm()
                else:
                    # дедлайн прошёл, пока приложение было мертво — досчитываем и будим
                    st.left = 0
                    st.running = False
                    do_finish()
            elif rl > 0 and rm in MODES:
                st.mode = rm
                st.total = rt if rt > 0 else dur(rm)
                st.left = min(rl, st.total)
                st.running = False
                st.ends_at = 0.0
                write_state(active=False)  # снимок «всё снято» после восстановления паузы
            refresh()
            page.update()
        except Exception:
            write_state()

    async def ack_loop():
        """Читает ack-файл нативной службы и ведёт баннер/статус в UI."""
        seen = ""
        while True:
            await asyncio.sleep(1.5)
            a = None
            try:
                with open(ACK_FILE, "r", encoding="utf-8") as f:
                    a = json.load(f)
            except Exception:
                a = None
            if isinstance(a, dict):
                aid = str(a.get("id") or "")
                if aid and aid != seen:
                    seen = aid
                    if a.get("ok") is False and a.get("error"):
                        notif_label.value = f"🔔 Служба будильника: ошибка — {a.get('error')}"
                        notif_label.color = "#FF8A80"
                    elif a.get("act") == "settings":
                        notif_label.value = "🔔 Открыты настройки уведомлений Android."
                        notif_label.color = MUTED
                    elif a.get("armed"):
                        try:
                            at = datetime.datetime.fromtimestamp(int(a.get("armed")) / 1000).strftime("%H:%M")
                        except Exception:
                            at = "?"
                        ex = ", точный" if a.get("exact") else ""
                        notif_label.value = f"🔔 Системный будильник: сработает в {at}{ex}"
                        notif_label.color = MUTED
                    else:
                        notif_label.value = "🔔 Будильник снят"
                        notif_label.color = MUTED
                # баннер: разрешение на уведомления (Kotlin проверяет сам)
                if a.get("notif") is False:
                    if banner_state["kind"] != "perm":
                        set_banner("perm", "Уведомления запрещены — в фоне не будет ни звука, ни вибрации. Разреши уведомления для Pomidor:")
                elif banner_state["kind"] == "perm":
                    set_banner(None, "")
            # таймер идёт, но служба не подтвердила последний снимок
            if (st.running and _last_state_write[0] > 0
                    and time.time() - _last_state_write[0] > 6 and seen != _gen[0]):
                if banner_state["kind"] is None:
                    set_banner("stale", "Служба будильника не отвечает. Полностью закрой и снова открой приложение.")
            elif banner_state["kind"] == "stale":
                set_banner(None, "")
            try:
                notif_label.update()
            except Exception:
                try:
                    page.update()
                except Exception:
                    pass

    page.add(ft.Stack([body, overlay], expand=True))
    refresh()
    page.update()
    page.run_task(loop)
    page.run_task(load_prefs)
    page.run_task(ack_loop)


if __name__ == "__main__":
    ft.app(main)

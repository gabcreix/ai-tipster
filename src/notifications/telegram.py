import requests
from loguru import logger
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def _send(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram no configurado — revisa TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env")
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        logger.error(f"Telegram {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def _format_picks(picks: list) -> str:
    by_tournament: dict = {}
    for p in picks:
        by_tournament.setdefault(p["tournament"], []).append(p)

    lines = ["🎾 <b>AI Tipster — Picks del día</b>\n"]

    for tournament, t_picks in by_tournament.items():
        surface = t_picks[0]["surface"]
        tour    = t_picks[0]["tour"]
        lines.append(f"🏆 <b>{tournament.replace('tennis_', '').replace('_', ' ').title()}</b>  {tour} · {surface}")
        lines.append("――――――――――――――――――――――――――")

        # Una línea por match+outcome (mejor cuota si hay varias casas)
        seen: dict = {}
        for p in sorted(t_picks, key=lambda x: x["odd"], reverse=True):
            key = (p["match"], p["outcome"])
            if key not in seen:
                seen[key] = p

        for pick in seen.values():
            rival = pick["match"].replace(pick["outcome"], "").replace(" vs ", "").strip()
            lines.append(
                f"\n✅ <b>{pick['outcome']}</b> vs {rival}\n"
                f"   📌 {pick['bookmaker']} @ <b>{pick['odd']}</b>\n"
                f"   📊 EV: <b>{pick['ev']:+.1%}</b>  |  Stake: <b>€{pick['stake_eur']}</b>\n"
                f"   🔮 Modelo {pick['our_prob']:.0%}  vs  Mercado {pick['market_prob']:.0%}"
            )
        lines.append("")

    total_stake = sum(p["stake_eur"] for p in picks)
    lines.append(f"💼 <b>Stake total: €{total_stake:.2f}</b>  ({len(picks)} pick(s))")
    return "\n".join(lines)


def send_picks(picks: list) -> bool:
    if not picks:
        return _send("⚪ <b>AI Tipster</b> — Sin valor detectado hoy.")
    return _send(_format_picks(picks))


def send_roi_summary(rows: list) -> bool:
    if not rows:
        return _send("📊 <b>ROI Summary</b> — Sin picks con resultado aún.")

    lines = ["📊 <b>AI Tipster — ROI acumulado</b>\n"]
    for r in rows:
        roi = r["roi_pct"] if r["roi_pct"] is not None else 0
        emoji = "🟢" if roi > 0 else "🔴"
        lines.append(
            f"{emoji} <b>{r['tour']} {r['surface']}</b>\n"
            f"   {r['wins']}W/{r['losses']}L  |  €{r['staked']} apostado  |  "
            f"P&L: €{r['profit']:+.2f}  |  ROI: <b>{roi:+.1f}%</b>"
        )
    return _send("\n".join(lines))

import requests
import time
import logging
import sys
from datetime import datetime

# Setup logging with Unicode support
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    file_handler = logging.FileHandler("trading_alerts.log", encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger

logger = setup_logging()

class BinanceFuturesAlert:
    def __init__(self, telegram_bot_token=None, telegram_chat_id=None):
        self.base_url = "https://fapi.binance.com"  # ✅ Fixed: no trailing spaces
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.previous_highs = {}
        self.previous_lows = {}

    def get_top_gainers(self, limit=20):
        """Fetch top 20 gainers by 24h price change % (USDT pairs only)"""
        try:
            response = requests.get(f"{self.base_url}/fapi/v1/ticker/24hr")
            response.raise_for_status()
            data = response.json()
            usdt_pairs = [d for d in data if d['symbol'].endswith('USDT')]
            sorted_pairs = sorted(usdt_pairs, key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_symbols = [pair['symbol'] for pair in sorted_pairs[:limit]]
            logger.info(f"Fetched top {len(top_symbols)} gainers: {', '.join(top_symbols)}")
            return top_symbols
        except Exception as e:
            logger.error(f"Error fetching top gainers: {e}")
            return []

    def get_klines(self, symbol, interval='1h', limit=2):
        try:
            params = {'symbol': symbol, 'interval': interval, 'limit': limit}
            response = requests.get(f"{self.base_url}/fapi/v1/klines", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return None

    def get_current_price(self, symbol):
        try:
            params = {'symbol': symbol}
            response = requests.get(f"{self.base_url}/fapi/v1/ticker/price", params=params)
            response.raise_for_status()
            return float(response.json()['price'])
        except Exception as e:
            logger.error(f"Error fetching current price for {symbol}: {e}")
            return None

    def get_24h_gain(self, symbol):
        try:
            params = {'symbol': symbol}
            response = requests.get(f"{self.base_url}/fapi/v1/ticker/24hr", params=params)
            response.raise_for_status()
            return float(response.json()['priceChangePercent'])
        except Exception as e:
            logger.error(f"Error fetching 24h gain for {symbol}: {e}")
            return None

    def check_cross_above_high(self, symbol):
        klines = self.get_klines(symbol)
        if not klines or len(klines) < 2:
            return False
        prev_high = float(klines[0][2])  # index 2 = high
        current_price = self.get_current_price(symbol)
        if current_price is None:
            return False
        crossed = current_price > prev_high
        self.previous_highs[symbol] = prev_high
        return crossed

    def check_cross_below_low(self, symbol):
        klines = self.get_klines(symbol)
        if not klines or len(klines) < 2:
            return False
        prev_low = float(klines[0][3])  # index 3 = low
        current_price = self.get_current_price(symbol)
        if current_price is None:
            return False
        crossed = current_price < prev_low
        self.previous_lows[symbol] = prev_low
        return crossed

    def send_telegram_alert(self, message):
        if not self.telegram_bot_token or not self.telegram_chat_id:
            logger.warning("Telegram not configured")
            return False
        try:
            # ✅ Critical fix: NO space after /bot
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, data=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")
            return False

    def send_alert(self, symbol, current_price, reference_price, breakout_type='high'):
        gain_24h = self.get_24h_gain(symbol)
        gain_emoji = "📈" if gain_24h and gain_24h > 0 else "📉" if gain_24h and gain_24h < 0 else "➡️"

        if breakout_type == 'high':
            direction = "above 1H candle high"
            emoji = "🚀"
            ref_label = "Previous High"
        else:
            direction = "below 1H candle low"
            emoji = "🔻"
            ref_label = "Previous Low"

        log_msg = (
            f"ALERT: {symbol} crossed {direction}!\n"
            f"Current: ${current_price:.6f}\n"
            f"{ref_label}: ${reference_price:.6f}\n"
            f"24h Gain: {gain_24h:.2f}%\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info(log_msg)

        telegram_msg = (
            f"<b>{emoji} Binance Futures Alert</b>\n\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Action:</b> Crossed {direction}\n"
            f"<b>Current Price:</b> ${current_price:.6f}\n"
            f"<b>{ref_label}:</b> ${reference_price:.6f}\n"
            f"<b>24h Gain:</b> {gain_emoji} {gain_24h:.2f}%\n"
            f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.send_telegram_alert(telegram_msg)

    def monitor(self, check_interval=60):
        logger.info("Starting simplified Binance Futures breakout monitor (Top 20 gainers only)")
        last_checked_hour = None

        while True:
            try:
                now = datetime.now()
                current_minute = now.minute
                current_hour = now.hour

                # Refresh top gainers every hour at minute 0
                if current_minute == 0:
                    symbols = self.get_top_gainers(limit=20)
                    logger.info("⏰ Checking breakouts at 1H candle close...")

                    for symbol in symbols:
                        current_price = self.get_current_price(symbol)
                        if current_price is None:
                            continue

                        if self.check_cross_above_high(symbol):
                            self.send_alert(symbol, current_price, self.previous_highs[symbol], 'high')
                        elif self.check_cross_below_low(symbol):
                            self.send_alert(symbol, current_price, self.previous_lows[symbol], 'low')

                    last_checked_hour = current_hour

                time.sleep(check_interval)

            except Exception as e:
                logger.error(f"Main loop error: {e}")
                time.sleep(60)

if __name__ == "__main__":
    TELEGRAM_BOT_TOKEN = "8255102897:AAEjtQGUk4c9eUuruW0nYoQBJOGI-uevLik"
    TELEGRAM_CHAT_ID = "-1002915874071"

    alert_system = BinanceFuturesAlert(
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )
    alert_system.monitor(check_interval=60)

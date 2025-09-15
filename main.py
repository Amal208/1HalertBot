import requests
import time
import pandas as pd
from datetime import datetime
import logging
import sys

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
        # ✅ FIXED: Removed trailing spaces in URL
        self.base_url = "https://fapi.binance.com"
        self.symbols = []
        self.symbols_24h_gain = {}
        self.previous_highs = {}
        self.previous_lows = {}
        # ✅ REMOVED: self.alerted_symbols (no more deduplication)
        self.symbol_entry_times = {}
        self.persistent_symbols = set()
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        
    def get_top_gaining_symbols(self, top_gainers_limit=15, mid_cap_limit=10, low_cap_limit=5):
        """Get mixed list: Top gainers from Large, Mid, and Low Cap tiers (by volume)"""
        try:
            url = f"{self.base_url}/fapi/v1/ticker/24hr"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            usdt_pairs = [d for d in data if d['symbol'].endswith('USDT')]
            if not usdt_pairs:
                logger.warning("No USDT pairs found.")
                return []

            # Sort by gain (for selection) and by volume (for cap tiering)
            sorted_by_gain = sorted(usdt_pairs, key=lambda x: float(x['priceChangePercent']), reverse=True)
            sorted_by_volume = sorted(usdt_pairs, key=lambda x: float(x['quoteVolume']), reverse=True)
            
            total = len(sorted_by_volume)
            if total == 0:
                return []

            # Define cap tiers by volume percentile
            large_cap_cutoff = max(1, int(total * 0.3))
            mid_cap_cutoff = max(large_cap_cutoff + 1, int(total * 0.7))
            
            large_cap_list = sorted_by_volume[:large_cap_cutoff]
            mid_cap_list = sorted_by_volume[large_cap_cutoff:mid_cap_cutoff]
            low_cap_list = sorted_by_volume[mid_cap_cutoff:]
            
            selected_symbols = set()
            final_list = []

            # Helper to add symbols without duplication
            def add_symbol(pair, category_name, emoji):
                symbol = pair['symbol']
                if symbol in selected_symbols:
                    return False
                gain = float(pair['priceChangePercent'])
                final_list.append(symbol)
                selected_symbols.add(symbol)
                self.symbols_24h_gain[symbol] = gain
                
                if symbol not in self.symbol_entry_times:
                    self.symbol_entry_times[symbol] = datetime.now()
                    logger.info(f"{emoji} {category_name}: {symbol} added at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                return True

            # Add Top Gainers from Large Cap
            for pair in sorted_by_gain:
                if len(final_list) >= top_gainers_limit + mid_cap_limit + low_cap_limit:
                    break
                if pair in large_cap_list:
                    add_symbol(pair, "Top Gainer (Large Cap)", "🆕")

            # Add Top Gainers from Mid Cap
            for pair in sorted_by_gain:
                if len(final_list) >= top_gainers_limit + mid_cap_limit + low_cap_limit:
                    break
                if pair in mid_cap_list:
                    add_symbol(pair, "Mid-Cap Pick", "📈")

            # Add Top Gainers from Low Cap
            for pair in sorted_by_gain:
                if len(final_list) >= top_gainers_limit + mid_cap_limit + low_cap_limit:
                    break
                if pair in low_cap_list:
                    add_symbol(pair, "Low-Cap Gem", "🚀")

            logger.info(f"✅ Watchlist: {len(final_list)} symbols ({top_gainers_limit} Large, {mid_cap_limit} Mid, {low_cap_limit} Low Cap)")
            return final_list
            
        except Exception as e:
            logger.error(f"Error fetching mixed symbols: {e}")
            return []

    def get_klines(self, symbol, interval='1h', limit=2):
        try:
            url = f"{self.base_url}/fapi/v1/klines"
            params = {'symbol': symbol, 'interval': interval, 'limit': limit}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return None

    def get_current_price(self, symbol):
        try:
            url = f"{self.base_url}/fapi/v1/ticker/price"
            params = {'symbol': symbol}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data['price'])
        except Exception as e:
            logger.error(f"Error fetching current price for {symbol}: {e}")
            return None

    def get_24h_gain(self, symbol):
        try:
            if symbol in self.symbols_24h_gain:
                return self.symbols_24h_gain[symbol]
            url = f"{self.base_url}/fapi/v1/ticker/24hr"
            params = {'symbol': symbol}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data['priceChangePercent'])
        except Exception as e:
            logger.error(f"Error fetching 24h gain for {symbol}: {e}")
            return None

    def check_cross_above_high(self, symbol):
        klines = self.get_klines(symbol, interval='1h')
        if not klines or len(klines) < 2:
            return False
        prev_high = float(klines[0][2])  # index 2 = high
        current_price = self.get_current_price(symbol)
        if current_price is None:
            return False
        crossed = current_price > prev_high and prev_high > 0
        self.previous_highs[symbol] = prev_high
        return crossed

    def check_cross_below_low(self, symbol):
        klines = self.get_klines(symbol, interval='1h')
        if not klines or len(klines) < 2:
            return False
        prev_low = float(klines[0][3])  # index 3 = low
        current_price = self.get_current_price(symbol)
        if current_price is None:
            return False
        crossed = current_price < prev_low and prev_low > 0
        self.previous_lows[symbol] = prev_low
        return crossed

    def is_persistent_symbol(self, symbol):
        if symbol not in self.symbol_entry_times:
            return False
        entry_time = self.symbol_entry_times[symbol]
        duration = datetime.now() - entry_time
        return duration.total_seconds() > (2 * 24 * 60 * 60)

    def send_telegram_alert(self, message):
        if not self.telegram_bot_token or not self.telegram_chat_id:
            logger.warning("Telegram bot token or chat ID not configured")
            return False
        try:
            # ✅ FIXED: Removed space after /bot
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, data=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error sending Telegram alert: {e}")
            return False

    def send_alert(self, symbol, current_price, reference_price, breakout_type='high'):
        gain_24h = self.get_24h_gain(symbol) or 0.0
        gain_emoji = "📈" if gain_24h > 0 else "📉" if gain_24h < 0 else "➡️"
        is_persistent = self.is_persistent_symbol(symbol)
        persistence_emoji = "🌟" if is_persistent else ""

        if breakout_type == 'high':
            direction = "above 1H candle high"
            emoji = "🚀"
            ref_label = "Previous High"
        elif breakout_type == 'low':
            direction = "below 1H candle low"
            emoji = "🔻"
            ref_label = "Previous Low"
        else:
            direction = "unknown breakout"
            emoji = "⚠️"
            ref_label = "Reference Price"

        # Log message
        log_message = f"ALERT: {persistence_emoji}{symbol} crossed {direction}!\n" \
                      f"Current: ${current_price:.6f}\n" \
                      f"{ref_label}: ${reference_price:.6f}\n" \
                      f"24h Gain: {gain_24h:.2f}%\n" \
                      f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        logger.info(log_message)

        # Telegram message
        telegram_message = f"<b>{persistence_emoji}{emoji} Binance Futures Alert</b>\n\n" \
                           f"<b>Symbol:</b> {symbol} {persistence_emoji}\n" \
                           f"<b>Action:</b> Crossed {direction}\n" \
                           f"<b>Current Price:</b> ${current_price:.6f}\n" \
                           f"<b>{ref_label}:</b> ${reference_price:.6f}\n" \
                           f"<b>24h Gain:</b> {gain_emoji} {gain_24h:.2f}%\n" \
                           f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        self.send_telegram_alert(telegram_message)

    def monitor(self, check_interval=300):
        logger.info("🚀 Starting Binance Futures Monitoring Alert System (Breakouts at 1H Close)")
        
        while True:
            try:
                # Refresh symbol list every hour
                if not self.symbols or datetime.now().minute == 0:
                    logger.info("🔄 Refreshing top gaining symbols and cap-tiered watchlist...")
                    self.symbols = self.get_top_gaining_symbols()
                    # ✅ REMOVED: self.alerted_symbols.clear() — allow repeat alerts
                    logger.info(f"Updated monitoring list: {len(self.symbols)} symbols")

                    # Log newly persistent symbols
                    current_time = datetime.now()
                    for symbol in self.symbols:
                        if symbol in self.symbol_entry_times and symbol not in self.persistent_symbols:
                            entry_time = self.symbol_entry_times[symbol]
                            if (current_time - entry_time).total_seconds() > (2 * 24 * 60 * 60):
                                logger.info(f"🌟 Persistent Momentum: {symbol} has been in watchlist for over 2 days (since {entry_time.strftime('%Y-%m-%d %H:%M:%S')})")
                                self.persistent_symbols.add(symbol)

                # ✅ ONLY check for breakouts at the top of the hour (when 1H candle closes)
                if datetime.now().minute == 0:
                    logger.info("⏰ Checking for 1H candle breakouts...")
                    for symbol in self.symbols:
                        try:
                            current_price = self.get_current_price(symbol)
                            if current_price is None:
                                continue

                            if self.check_cross_above_high(symbol):
                                self.send_alert(symbol, current_price, self.previous_highs[symbol], breakout_type='high')

                            elif self.check_cross_below_low(symbol):
                                self.send_alert(symbol, current_price, self.previous_lows[symbol], breakout_type='low')

                        except Exception as e:
                            logger.error(f"Error checking {symbol}: {e}")

                time.sleep(check_interval)
                
            except KeyboardInterrupt:
                logger.info("🛑 Monitoring stopped by user.")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(check_interval)

if __name__ == "__main__":
    # ⚠️ Replace with your actual credentials
    TELEGRAM_BOT_TOKEN = "8255102897:AAEjtQGUk4c9eUuruW0nYoQBJOGI-uevLik"
    TELEGRAM_CHAT_ID = "-1002915874071"
    
    alert_system = BinanceFuturesAlert(
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )
    
    # Start monitoring (checks every 5 mins, but breakouts only evaluated hourly)
    alert_system.monitor(check_interval=300)

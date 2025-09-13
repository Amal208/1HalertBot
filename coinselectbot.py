import requests
import time
import pandas as pd
from datetime import datetime
import logging
import sys

# Setup logging with Unicode support
def setup_logging():
    # Create logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    
    # File handler with UTF-8 encoding
    file_handler = logging.FileHandler("trading_alerts.log", encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Stream handler with UTF-8 encoding for console
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    return logger

# Setup logging
logger = setup_logging()

class BinanceFuturesAlert:
    def __init__(self, telegram_bot_token=None, telegram_chat_id=None):
        # FIXED: Removed trailing spaces
        self.base_url = "https://fapi.binance.com"
        self.symbols = []
        self.symbols_24h_gain = {}      # Store 24h gain for each symbol
        self.previous_highs = {}        # Store previous 1H candle high
        self.previous_lows = {}         # Store previous 1H candle low
        self.alerted_symbols = set()    # Prevent duplicate alerts
        self.symbol_entry_times = {}    # Track when symbol FIRST entered Top 50
        self.persistent_symbols = set() # Cache symbols known to be persistent
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        
    def get_top_gaining_symbols(self, limit=30):
        """Get top gaining futures symbols by 24h price change percentage"""
        try:
            url = f"{self.base_url}/fapi/v1/ticker/24hr"
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Filter for USDT pairs and sort by price change percent
            usdt_pairs = [d for d in data if d['symbol'].endswith('USDT')]
            sorted_pairs = sorted(usdt_pairs, key=lambda x: float(x['priceChangePercent']), reverse=True)
            
            # Get top gaining symbols and their 24h gain
            top_symbols = []
            for pair in sorted_pairs[:limit]:
                symbol = pair['symbol']
                gain = float(pair['priceChangePercent'])
                top_symbols.append(symbol)
                self.symbols_24h_gain[symbol] = gain

                # Track first entry time
                if symbol not in self.symbol_entry_times:
                    self.symbol_entry_times[symbol] = datetime.now()
                    logger.info(f"🆕 New Top Gainer: {symbol} entered list at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            return top_symbols
            
        except Exception as e:
            logger.error(f"Error fetching top gaining symbols: {e}")
            return []
    
    def get_klines(self, symbol, interval='1h', limit=2):
        """Get candle data for a symbol"""
        try:
            url = f"{self.base_url}/fapi/v1/klines"
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }
            response = requests.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return None
    
    def get_current_price(self, symbol):
        """Get current price for a symbol"""
        try:
            url = f"{self.base_url}/fapi/v1/ticker/price"
            params = {'symbol': symbol}
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return float(data['price'])
        except Exception as e:
            logger.error(f"Error fetching current price for {symbol}: {e}")
            return None
    
    def get_24h_gain(self, symbol):
        """Get 24h gain percentage for a symbol"""
        try:
            # If we already have the gain from the top symbols list, use it
            if symbol in self.symbols_24h_gain:
                return self.symbols_24h_gain[symbol]
            
            # Otherwise, fetch it individually
            url = f"{self.base_url}/fapi/v1/ticker/24hr"
            params = {'symbol': symbol}
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return float(data['priceChangePercent'])
        except Exception as e:
            logger.error(f"Error fetching 24h gain for {symbol}: {e}")
            return None
    
    def check_cross_above_high(self, symbol):
        """Check if current price has crossed above previous candle's high"""
        klines = self.get_klines(symbol, interval='1h')
        if not klines or len(klines) < 2:
            return False
        
        prev_high = float(klines[0][2])  # High price is at index 2
        current_price = self.get_current_price(symbol)
        if current_price is None:
            return False
        
        if symbol not in self.previous_highs:
            self.previous_highs[symbol] = prev_high
        
        crossed = current_price > self.previous_highs[symbol] and self.previous_highs[symbol] > 0
        self.previous_highs[symbol] = prev_high
        
        return crossed

    def check_cross_below_low(self, symbol):
        """Check if current price has crossed below previous candle's low"""
        klines = self.get_klines(symbol, interval='1h')
        if not klines or len(klines) < 2:
            return False
        
        prev_low = float(klines[0][3])  # Low price is at index 3
        current_price = self.get_current_price(symbol)
        if current_price is None:
            return False
        
        if symbol not in self.previous_lows:
            self.previous_lows[symbol] = prev_low
        
        crossed = current_price < self.previous_lows[symbol] and self.previous_lows[symbol] > 0
        self.previous_lows[symbol] = prev_low
        
        return crossed

    def is_persistent_symbol(self, symbol):
        """Check if symbol has been in Top 50 for more than 2 days"""
        if symbol not in self.symbol_entry_times:
            return False
        
        entry_time = self.symbol_entry_times[symbol]
        duration = datetime.now() - entry_time
        return duration.total_seconds() > (2 * 24 * 60 * 60)  # 2 days in seconds
    
    def send_telegram_alert(self, message):
        """Send alert to Telegram"""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            logger.warning("Telegram bot token or chat ID not configured")
            return False
        
        try:
            # FIXED: Removed space after /bot
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
            logger.error(f"Error sending Telegram alert: {e}")
            return False
    
    def send_alert(self, symbol, current_price, reference_price, breakout_type='high'):
        """Send alert to both log and Telegram"""
        gain_24h = self.get_24h_gain(symbol)
        gain_emoji = "📈" if gain_24h and gain_24h > 0 else "📉" if gain_24h and gain_24h < 0 else "➡️"
        
        # Check if symbol is persistent (>2 days in Top 50)
        is_persistent = self.is_persistent_symbol(symbol)
        persistence_tag = "🌟 PERSISTENT " if is_persistent else ""
        persistence_emoji = "🌟" if is_persistent else ""

        # Determine alert type and emoji
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

        # Log message (without emojis for Windows compatibility)
        log_message = f"ALERT: {persistence_tag}{symbol} crossed {direction}!\n" \
                      f"Current: ${current_price:.6f}\n" \
                      f"{ref_label}: ${reference_price:.6f}\n" \
                      f"24h Gain: {gain_24h:.2f}%\n" \
                      f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        logger.info(log_message)
        
        # Telegram message (with emojis)
        telegram_message = f"<b>{persistence_emoji}{emoji} Binance Futures Alert</b>\n\n" \
                           f"<b>Symbol:</b> {symbol} {'🌟' if is_persistent else ''}\n" \
                           f"<b>Action:</b> Crossed {direction}\n" \
                           f"<b>Current Price:</b> ${current_price:.6f}\n" \
                           f"<b>{ref_label}:</b> ${reference_price:.6f}\n" \
                           f"<b>24h Gain:</b> {gain_emoji} {gain_24h:.2f}%\n" \
                           f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        self.send_telegram_alert(telegram_message)
    
    def monitor(self, check_interval=300):
        """Main monitoring loop"""
        logger.info("Starting Binance Futures Monitoring Alert System (1H Timeframe)")
        
        while True:
            try:
                # Refresh top gaining symbols every hour
                if not self.symbols or datetime.now().minute == 0:
                    self.symbols = self.get_top_gaining_symbols()
                    self.alerted_symbols.clear()  # Reset alerts for new symbols
                    logger.info(f"Updated monitoring list: {len(self.symbols)} symbols")

                    # Optional: Log newly persistent symbols
                    current_time = datetime.now()
                    for symbol in self.symbols:
                        if symbol in self.symbol_entry_times and symbol not in self.persistent_symbols:
                            entry_time = self.symbol_entry_times[symbol]
                            if (current_time - entry_time).total_seconds() > (2 * 24 * 60 * 60):
                                logger.info(f"🌟 Persistent Momentum: {symbol} has been in Top 50 for over 2 days (since {entry_time.strftime('%Y-%m-%d %H:%M:%S')})")
                                self.persistent_symbols.add(symbol)
                
                # Check each symbol
                for symbol in self.symbols:
                    try:
                        current_price = self.get_current_price(symbol)
                        if current_price is None:
                            continue

                        # Check for breakout above HIGH
                        if self.check_cross_above_high(symbol) and symbol not in self.alerted_symbols:
                            self.send_alert(symbol, current_price, self.previous_highs[symbol], breakout_type='high')
                            self.alerted_symbols.add(symbol)

                        # Check for breakout below LOW
                        elif self.check_cross_below_low(symbol) and symbol not in self.alerted_symbols:
                            self.send_alert(symbol, current_price, self.previous_lows[symbol], breakout_type='low')
                            self.alerted_symbols.add(symbol)

                    except Exception as e:
                        logger.error(f"Error checking {symbol}: {e}")
                
                time.sleep(check_interval)
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(check_interval)

if __name__ == "__main__":
    # Configure your Telegram bot token and chat ID
    TELEGRAM_BOT_TOKEN = "8255102897:AAEjtQGUk4c9eUuruW0nYoQBJOGI-uevLik"
    TELEGRAM_CHAT_ID = "-1002915874071"
    
    # Initialize the alert system
    alert_system = BinanceFuturesAlert(
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )
    
    # Start monitoring with 5-minute intervals
    alert_system.monitor(check_interval=900)
import math

from dexbot.basestrategy import BaseStrategy
from dexbot.queue.idle_queue import idle_add


class Strategy(BaseStrategy):
    """ Staggered Orders strategy
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Define Callbacks
        self.onMarketUpdate += self.check_orders
        self.onAccount += self.check_orders

        self.error_ontick = self.error
        self.error_onMarketUpdate = self.error
        self.error_onAccount = self.error

        self.worker_name = kwargs.get('name')
        self.view = kwargs.get('view')
        self.amount = self.worker['amount']
        self.spread = self.worker['spread'] / 100
        self.increment = self.worker['increment'] / 100
        self.upper_bound = self.worker['upper_bound']
        self.lower_bound = self.worker['lower_bound']

        if self['setup_done']:
            self.place_orders()
        else:
            self.init_strategy()

        if self.view:
            self.update_gui_profit()
            self.update_gui_slider()

    def error(self, *args, **kwargs):
        self.cancel_all()
        self.disabled = True
        self.log.info(self.execute())

    def init_strategy(self):
        # Make sure no orders remain
        self.cancel_all()
        self.clear_orders()

        center_price = self.calculate_center_price()
        amount = self.amount
        spread = self.spread
        increment = self.increment
        lower_bound = self.lower_bound
        upper_bound = self.upper_bound

        # Calculate buy prices
        buy_prices = self.calculate_buy_prices(center_price, spread, increment, lower_bound)

        # Calculate sell prices
        sell_prices = self.calculate_sell_prices(center_price, spread, increment, upper_bound)

        # Calculate buy and sell amounts
        buy_orders, sell_orders = self.calculate_amounts(buy_prices, sell_prices, amount, spread, increment)

        # Make sure there is enough balance for the buy orders
        needed_buy_asset = 0
        for buy_order in buy_orders:
            needed_buy_asset += buy_order['amount'] * buy_order['price']
        if self.balance(self.market["base"]) < needed_buy_asset:
            self.log.critical(
                "Insufficient buy balance, needed {} {}".format(needed_buy_asset, self.market['base']['symbol'])
            )
            self.disabled = True
            return

        # Make sure there is enough balance for the sell orders
        needed_sell_asset = 0
        for sell_order in sell_orders:
            needed_sell_asset += sell_order['amount']
        if self.balance(self.market["quote"]) < needed_sell_asset:
            self.log.critical(
                "Insufficient sell balance, needed {} {}".format(needed_sell_asset, self.market['quote']['symbol'])
            )
            self.disabled = True
            return

        # Place the buy orders
        for buy_order in buy_orders:
            order = self.market_buy(buy_order['amount'], buy_order['price'])
            self.save_order(order)

        # Place the sell orders
        for sell_order in sell_orders:
            order = self.market_sell(sell_order['amount'], sell_order['price'])
            self.save_order(order)

        self['setup_done'] = True

    def replace_order(self, order):
        """ Replaces an order with a reverse order
            buy orders become sell orders and sell orders become buy orders
        """
        self.log.info('Change detected, updating orders')
        self.remove_order(order)

        if order['base']['symbol'] == self.market['base']['symbol']:  # Buy order
            price = order['price'] * self.spread
            amount = order['quote']['amount']
            new_order = self.market_sell(amount, price)
        else:  # Sell order
            price = (order['price'] ** -1) / self.spread
            amount = order['quote']['amount']
            new_order = self.market_buy(amount, price)

        self.save_order(new_order)

    def place_order(self, order):
        if order['base']['symbol'] == self.market['base']['symbol']:  # Buy order
            price = order['price']
            amount = order['quote']['amount']
            self.market_buy(amount, price)
        else:  # Sell order
            price = order['price'] ** -1
            amount = order['base']['amount']
            self.market_sell(amount, price)

    def place_orders(self):
        """ Place all the orders found in the database
        """
        self.cancel_all()
        orders = self.fetch_orders()
        for order_id, order in orders.items():
            self.place_order(order)

    def check_orders(self, *args, **kwargs):
        """ Tests if the orders need updating
        """
        orders = self.fetch_orders()
        for order_id, order in orders.items():
            current_order = self.get_order(order)
            if not current_order:
                self.replace_order(order)

        if self.view:
            self.update_gui_profit()
            self.update_gui_slider()

    @staticmethod
    def calculate_buy_prices(center_price, spread, increment, lower_bound):
        buy_prices = []
        if lower_bound > center_price / math.sqrt(1 + spread):
            return buy_prices

        buy_price = center_price / math.sqrt(1 + spread)
        while buy_price > lower_bound:
            buy_prices.append(buy_price)
            buy_price = buy_price / (1 + increment)
        return buy_prices

    @staticmethod
    def calculate_sell_prices(center_price, spread, increment, upper_bound):
        sell_prices = []
        if upper_bound < center_price * math.sqrt(1 + spread):
            return sell_prices

        sell_price = center_price * math.sqrt(1 + spread)
        while sell_price < upper_bound:
            sell_prices.append(sell_price)
            sell_price = sell_price * (1 + increment)
        return sell_prices

    @staticmethod
    def calculate_amounts(buy_prices, sell_prices, amount, spread, increment):
        # Calculate buy amounts
        buy_orders = []
        if buy_prices:
            highest_buy_price = buy_prices.pop(0)
            buy_orders.append({'amount': amount, 'price': highest_buy_price})
            for buy_price in buy_prices:
                last_amount = buy_orders[-1]['amount']
                current_amount = last_amount * math.sqrt(1 + increment)
                buy_orders.append({'amount': current_amount, 'price': buy_price})

        # Calculate sell amounts
        sell_orders = []
        if sell_prices:
            lowest_sell_price = sell_prices.pop(0)
            current_amount = amount * math.sqrt(1 + spread + increment)
            sell_orders.append({'amount': current_amount, 'price': lowest_sell_price})
            for sell_price in sell_prices:
                last_amount = sell_orders[-1]['amount']
                current_amount = last_amount / math.sqrt(1 + increment)
                sell_orders.append({'amount': current_amount, 'price': sell_price})

        return [buy_orders, sell_orders]

    @staticmethod
    def get_required_assets(market, amount, spread, increment, lower_bound, upper_bound):
        if not amount or not lower_bound or not increment:
            return None

        ticker = market.ticker()
        highest_bid = ticker.get("highestBid")
        lowest_ask = ticker.get("lowestAsk")
        if not float(highest_bid):
            return None
        elif not float(lowest_ask):
            return None
        else:
            center_price = (highest_bid['price'] + lowest_ask['price']) / 2

        # Calculate buy prices
        buy_prices = Strategy.calculate_buy_prices(center_price, spread, increment, lower_bound)

        # Calculate sell prices
        sell_prices = Strategy.calculate_sell_prices(center_price, spread, increment, upper_bound)

        # Calculate buy and sell amounts
        buy_orders, sell_orders = Strategy.calculate_amounts(
            buy_prices, sell_prices, amount, spread, increment
        )

        needed_buy_asset = 0
        for buy_order in buy_orders:
            needed_buy_asset += buy_order['amount'] * buy_order['price']

        needed_sell_asset = 0
        for sell_order in sell_orders:
            needed_sell_asset += sell_order['amount']

        return [needed_buy_asset, needed_sell_asset]

    # GUI updaters
    def update_gui_profit(self):
        pass

    def update_gui_slider(self):
        ticker = self.market.ticker()
        latest_price = ticker.get('latest').get('price')
        total_balance = self.total_balance()
        total = (total_balance['quote'] * latest_price) + total_balance['base']

        if not total:  # Prevent division by zero
            percentage = 50
        else:
            percentage = (total_balance['base'] / total) * 100
        idle_add(self.view.set_worker_slider, self.worker_name, percentage)
        self['slider'] = percentage

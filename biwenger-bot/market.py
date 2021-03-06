from player import Player
import math
from lineup import LineUp
from numpy import interp, mean
from prices_predictor import PricesPredictor
from lineup import MIN_PLAYERS_BY_POS

MAX_MONEY_AT_BANK = 20000000

WEIGHT_ASSURE_MONEY = 0.5
WEIGHT_ASSURE_POINTS = 0.5

class Market:

    def __init__(self, cli, line_up):
        self.cli = cli
        self.line_up = line_up
        self.prices_predictor = PricesPredictor()
        self.my_squad = self.line_up.get_my_players()
        self.my_players_by_pos = line_up.get_players_by_pos(self.my_squad)
        self.min_players_by_pos = MIN_PLAYERS_BY_POS
        self.received_offers_from_computer = self.get_received_offers_from_computer()
        self.days_to_next_round = 0 # this was broken : self.get_days_to_next_round()
        self.players_in_market_from_computer = Player.get_players_from_player_ids(
            self.cli, self.get_players_ids_from_players_in_market_from_computer()
        )
        self.available_money = self.get_my_money()
        self.max_bid = self.get_max_bid()
        self.market_evolution = self.get_market_evolution()
        self.bided_today = 0
        self.buying_aggressivity = self.calculate_buying_aggressivity()
        self.selling_aggressivity = 10 - self.buying_aggressivity
        self.max_player_price_to_bid_for = 5000000 + self.buying_aggressivity * 500000
        self.min_market_points_to_bid = 10 - self.buying_aggressivity*0.7
        self.min_market_points_to_sell = -40 + self.selling_aggressivity*2
        self.aggresivity_percentage_to_add_to_bid = 1 + self.buying_aggressivity * 0.007
        self.team_points_mean_by_player = round(mean([p.points_mean for p in self.my_squad]), 4)
        self.team_points_fitness_by_player = round(mean([p.points_fitness for p in self.my_squad]), 4)
        self.team_points_by_player = round(mean([p.points for p in self.my_squad]), 4)
        self.normalized_team_points_mean_per_million = round(mean([p.points_mean_per_million*(1.4**int(p.price/1000000)) for p in self.my_squad]), 4)

    def place_offers_for_players_in_market(self):
        for player_in_market in self.players_in_market_from_computer:
            print("studying to make a bid for " + player_in_market.name)
            predicted_price = self.prices_predictor.predict_price(player_in_market)
            player_in_market.market_points = self.get_market_points(player_in_market, predicted_price)
            if self.should_place_offer(player_in_market):
                bid_price = self.calculate_bid_price(player_in_market)
                if self.do_i_have_money_to_bid(bid_price):
                    print("decided to make a bid for " + player_in_market.name + " for " + str(bid_price) + "$")
                    self.place_offer(player_in_market.id, bid_price)
                    self.my_players_by_pos = self.line_up.get_players_by_pos(self.my_squad)

    def study_offers_for_my_players(self):
        if self.received_offers_from_computer:
            for received_offer in self.received_offers_from_computer:
                player_id = received_offer["idPlayer"]
                player = Player.get_player_from_player_id(self.cli, player_id)
                print("studying to accept offer for " + player.name)
                prediction_price = self.prices_predictor.predict_price(player)
                player.market_points = self.get_market_points(player, prediction_price)
                offer_price = received_offer["ammount"]
                if not self.is_min_players_by_pos_guaranteed(player):
                    continue
                if self.should_accept_offer(player):
                    print("decided to accept offer for  " + player.name + " for " + str(offer_price) + "$")
                    self.accept_offer(received_offer["idOffer"])
                    self.my_players_by_pos = self.line_up.get_players_by_pos(self.my_squad)

    def place_all_my_players_to_market(self, price):
        place_players_to_market = PlacePlayersToMarket(price)
        self.cli.do_post("sendPlayersToMarket", place_players_to_market)

    def should_accept_offer(self, player):
        if player.market_points is not None and player.market_points < self.min_market_points_to_sell:
            return True
        return False

    def should_place_offer(self, player):
        return \
            player.status == "ok" and \
            player.market_points is not None and \
            player.market_points > self.min_market_points_to_bid and \
            player.price < self.max_player_price_to_bid_for

    def calculate_bid_price(self, player):
        player_millions_value = int(player.price/1000000)
        expensive_corrector_factor = 1-player_millions_value*0.01
        bid_percentage_to_multiply = float(interp(player.market_points, [0, 10, 25, 75, 100], [1, 1.05, 1.10, 1.20, 1.30]))*expensive_corrector_factor
        bid_price = player.price*bid_percentage_to_multiply*self.aggresivity_percentage_to_add_to_bid
        return int(max(bid_price, player.price))

    def accept_offer(self, offer_id):
        return self.cli.do_get("acceptReceivedOffer?id=" + str(offer_id))["data"]

    def place_offer(self, player_id, bid_price):
        offer = PlaceOffer(bid_price, [player_id], None, "purchase")
        self.cli.do_post("placeOffer", offer)["data"]
        self.bided_today += bid_price

    def is_min_players_by_pos_guaranteed(self, player: Player):
        players_in_this_pos = len(self.my_players_by_pos[player.position])
        min_players_in_this_pos = self.min_players_by_pos[player.position - 1]
        if players_in_this_pos <= min_players_in_this_pos:
            print("don't sell " + str(player.name) +
                  "! We just have " + str(players_in_this_pos) + " players in this position. " +
                  "At least " + str(min_players_in_this_pos) + " are required. ")
            return False
        return True

    def assure_positive_balance_before_next_round(self):
        while self.get_my_money() < 0 and self.days_to_next_round <= 1:
            for p in self.my_squad:
                prediction_price = self.prices_predictor.predict_price(p)
                market_points = self.get_market_points(p, prediction_price)
                self.set_assure_points(p, market_points, self.get_my_money())
            self.line_up.order_players_by_assure_points(self.my_squad)

            sold = False
            i = 0
            while not sold:
                candidate_player_to_sell = self.my_squad[i]
                if not self.is_min_players_by_pos_guaranteed(candidate_player_to_sell):
                    i = i+1
                else:
                    sold = True

            offer_to_accept = [o for o in self.received_offers_from_computer if o["idPlayer"] == candidate_player_to_sell.id][0]
            print("decided to accept offer for  " + candidate_player_to_sell.name + "for " + str(offer_to_accept["ammount"]) + " so we can get a positive balance!")
            self.accept_offer(offer_to_accept["idOffer"])
            self.my_squad = self.line_up.get_my_players()
            self.my_players_by_pos = self.line_up.get_players_by_pos(self.my_squad)
        print("Already have a positive balance!")



    def calculate_buying_aggressivity(self):
        money_aggressivity = self.available_money / MAX_MONEY_AT_BANK * 10
        dates_aggressivity = self.days_to_next_round*0.15
        return min(max(money_aggressivity+dates_aggressivity, 0), 10)

    def get_market_points(self, player: Player, predicted_price):
        diff_price_weight = 0.45
        diff_points_fitness_weight = 0.25
        diff_points_total_weight = 0.15
        diff_points_mean_weight = 0.1
        diff_points_per_million_weight = 0.05
        percentage_diff = (predicted_price - player.price) / player.price * 100
        normalized_diff = min(max(((percentage_diff) ** 3) / 25, -100), 100)

        player_millions_value = int(player.price/1000000)
        normalized_points_per_million = float(interp(player.points_mean_per_million*(1.4**player_millions_value), [0, self.normalized_team_points_mean_per_million, 10], [-100, 0, 100]))

        normalized_points_fitness = float(interp(player.points_fitness/self.team_points_fitness_by_player, [0, 1, 3], [-100, 0, 100]))
        normalized_points_total = float(interp(player.points/self.team_points_by_player, [0, 1, 3], [-100, 0, 100]))
        normalized_points_mean = float(interp(player.points_mean, [0, self.team_points_mean_by_player, 10], [-100, 0, 100]))

        market_points = round(
               diff_price_weight * normalized_diff + \
               diff_points_fitness_weight * normalized_points_fitness + \
               diff_points_per_million_weight * normalized_points_per_million + \
               diff_points_total_weight * normalized_points_total + \
               diff_points_mean_weight * normalized_points_mean, 4)
        print("market_points for " + player.name + ": " + str(market_points))
        return market_points

    def get_days_to_next_round(self):
        return self.cli.do_get("getDaysToNextRound")["data"]

    def get_received_offers(self):
        return self.cli.do_get("getReceivedOffers")["data"]

    def get_received_offers_from_computer(self):
        received_offers = self.get_received_offers()
        if received_offers is not None:
            return [x for x in received_offers if x["idUser"] == 0]

    def do_i_have_money_to_bid(self, bid_price):
        return self.available_money - self.bided_today - bid_price > -1000000*self.days_to_next_round

    def get_players_ids_from_players_in_market_from_computer(self):
        return [p['idPlayer'] for p in self.get_players_in_market_from_computer_with_price()]

    def get_players_in_market_with_price(self):
        return self.cli.do_get("getPlayersInMarket")["data"]

    def get_players_in_market_from_computer_with_price(self):
        return [p for p in self.get_players_in_market_with_price() if p["idUser"] == 0]

    def get_my_money(self):
        return self.cli.do_get("getMyMoney")["data"]

    def get_max_bid(self):
        return self.cli.do_get("getMaxBid")["data"]

    def get_market_evolution(self):
        return self.cli.do_get("getMarketEvolution")["data"]

    def set_assure_points(self, player, market_points, money_to_balance):
        normalized_price_over_total_debt = min(abs(player.price / money_to_balance), 1)
        assure_points = WEIGHT_ASSURE_MONEY*normalized_price_over_total_debt + WEIGHT_ASSURE_POINTS*-market_points
        print(player.name + " assure points : " + str(assure_points))
        player.assure_points = assure_points

class PlacePlayersToMarket:

    def __init__(self, price):
        self.price = price


class PlaceOffer:

    def __init__(self, amount, requested_players, to, type):
        self.amount = amount
        self.requestedPlayers = requested_players
        self.to = to
        self.type = type

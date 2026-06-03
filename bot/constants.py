"""
Game constants for Family Tree Bot economy, factory, garden, and cooking systems.

All prices, rewards, timings, and game balance values are centralized here.
"""

import math as _math
import os
from typing import Dict, Optional

# ============ ECONOMY CONSTANTS ============

CURRENCY_SYMBOL = "$"
STARTING_BALANCE = 100_000

# ============ FUNERAL CONSTANTS ============

# Default donation amount when user runs /funeral without an amount arg.
FUNERAL_DEFAULT_AMOUNT = 10_000_000

# File ID of the coffin video used in funeral messages. Get it via /fileid
# on the desired video, then paste the string here. Leave None to send a
# text-only funeral message.
COFFIN_VIDEO_FILE_ID: Optional[str] = (
    "BAACAgUAAyEFAATHn2dvAAIUvmoFRIITsk1YQdGvGz1tcSKW8NCHAAKeHAACI6QoVB9iUIR96jFuHgQ"
)

# Custom-emoji IDs used in the funeral caption (Telegram premium emojis).
FUNERAL_EMOJI_IDS = (
    "4958724224962265918",
    "5850607358304064081",
    "5850587957936787800",
)

# ============ GAMBLING CONSTANTS ============

# Ripple
RIPPLE_MULTIPLIER = 1.5
RIPPLE_MAX_LEVEL = 20
RIPPLE_MIN_BET = 10
RIPPLE_WARN_MIN_BET = 100_000
RIPPLE_MAX_BET = 1_000_000
RIPPLE_SUNFLOWER_CHANCE = 0.70  # 70% sunflower, 30% snake

# Rbet (text-based ripple)
RBET_MULTIPLIER = 1.5
RBET_SNAKE_CHANCE = 0.20  # 20% snake / 80% sunflower per round


# ============ FACTORY CONSTANTS ============

# Factory base settings
FACTORY_INITIAL_CAPACITY = 3  # Starting worker slots
FACTORY_WORK_DURATION = 60 * 60  # 1 hour in seconds
FACTORY_BASE_EARNING = 500  # Base earning per worker per shift

# Factory expansion costs
FACTORY_EXPANSION_COSTS = {
    4: 5000,
    5: 10000,
    6: 20000,
    7: 35000,
    8: 50000,
    9: 75000,
    10: 100000,
}

# Worker settings
WORKER_BASE_SALARY = 100  # Cost to hire a worker
WORKER_FEED_COST = 500  # Cost to reduce fatigue with money
WORKER_INITIAL_FACTORY_SLOTS = 2  # How many factories a new worker can work at

# Fatigue settings (0-100 scale)
WORKER_FATIGUE_PER_SHIFT = 10  # Fatigue gained per work shift (reduced from 20)
WORKER_FATIGUE_MAX = 100
WORKER_FATIGUE_THRESHOLD = 80  # Can't work if fatigue >= this

# Worker XP and leveling
WORKER_XP_PER_SHIFT = 10
WORKER_LEVEL_XP = {  # XP needed for each level
    1: 0,
    2: 100,
    3: 250,
    4: 500,
    5: 1000,
    6: 2000,
    7: 3500,
    8: 5000,
    9: 7500,
    10: 10000,
}

# Factory slots unlocked per worker level (level + 1)
WORKER_FACTORY_SLOTS = {
    1: 2,
    2: 3,
    3: 4,
    4: 5,
    5: 6,
    6: 7,
    7: 8,
    8: 9,
    9: 10,
    10: 11,
}


# ============ GARDEN CONSTANTS ============

# Garden grid settings
GARDEN_INITIAL_SIZE = 3  # 3x3 starting grid
GARDEN_MAX_SIZE = 100  # 100x100 max

# Garden expansion costs
GARDEN_EXPANSION_COSTS = {
    4: 2000,
    5: 5000,
    6: 10000,
    7: 20000,
    8: 35000,
    9: 50000,
    10: 75000,
}

# Fertilize settings (scales with garden size)
FERTILIZE_TIME_REDUCTION_BASE = 10 * 60  # 10 minutes base reduction
FERTILIZE_REWARD_PER_PLANT = 10  # $10 per plant fertilized
FERTILIZE_COOLDOWN = 7 * 60  # 7 minutes global cooldown per user
FERTILIZE_BOT_WINDOW_HOURS = 2  # detection window
FERTILIZE_BOT_THRESHOLD = (
    16  # max fertilizes in window before auto-ban (2h / 7min ≈ 17)
)
FERTILIZE_BOT_BAN_DAYS = 7  # ban duration in days
FERTILIZE_BOT_REPORT_WINDOW_HOURS = 5  # analysis window for owner notifications
FERTILIZE_RECEIVE_BOT_WINDOW_HOURS = (
    2  # detection window for receiving fertilizes
)
FERTILIZE_RECEIVE_BOT_THRESHOLD = (
    16  # max received fertilizes in window before auto-ban
)
FERTILIZE_RECEIVE_BOT_BAN_DAYS = 7  # ban duration in days


# ============ CROPS AND SEEDS ============

# Crop definitions
# Format: name -> {emoji, seed_cost, sell_price, grow_time_minutes, yield_min, yield_max}
CROPS = {
    "carrot": {
        "emoji": "🥕",
        "seed_cost": 350,
        "sell_price": 200,
        "grow_time": 30,  # minutes
        "yield_min": 2,
        "yield_max": 4,
    },
    "tomato": {
        "emoji": "🍅",
        "seed_cost": 400,
        "sell_price": 250,
        "grow_time": 45,
        "yield_min": 2,
        "yield_max": 4,
    },
    "corn": {
        "emoji": "🌽",
        "seed_cost": 350,
        "sell_price": 200,
        "grow_time": 40,
        "yield_min": 2,
        "yield_max": 5,
    },
    "potato": {
        "emoji": "🥔",
        "seed_cost": 300,
        "sell_price": 180,
        "grow_time": 35,
        "yield_min": 3,
        "yield_max": 6,
    },
    "pepper": {
        "emoji": "🌶️",
        "seed_cost": 500,
        "sell_price": 350,
        "grow_time": 60,
        "yield_min": 2,
        "yield_max": 4,
    },
    "eggplant": {
        "emoji": "🍆",
        "seed_cost": 550,
        "sell_price": 400,
        "grow_time": 70,
        "yield_min": 2,
        "yield_max": 3,
    },
    "lettuce": {
        "emoji": "🥬",
        "seed_cost": 250,
        "sell_price": 150,
        "grow_time": 25,
        "yield_min": 2,
        "yield_max": 4,
    },
    "broccoli": {
        "emoji": "🥦",
        "seed_cost": 450,
        "sell_price": 300,
        "grow_time": 50,
        "yield_min": 2,
        "yield_max": 3,
    },
    "onion": {
        "emoji": "🧅",
        "seed_cost": 300,
        "sell_price": 180,
        "grow_time": 35,
        "yield_min": 3,
        "yield_max": 5,
    },
    "garlic": {
        "emoji": "🧄",
        "seed_cost": 350,
        "sell_price": 220,
        "grow_time": 40,
        "yield_min": 3,
        "yield_max": 5,
    },
    "cocoa_beans": {
        "emoji": "☕️",
        "custom_emoji_id": "4990198952193164384",
        "seed_cost": 1_800,
        "sell_price": 500,
        "grow_time": 90,  # 1h 30m
        "yield_min": 3,
        "yield_max": 5,
    },
    "wheat": {
        "emoji": "🌾",
        "seed_cost": 300,
        "sell_price": 150,
        "grow_time": 30,
        "yield_min": 3,
        "yield_max": 6,
    },
    "soybean": {
        "emoji": "🫘",
        "seed_cost": 350,
        "sell_price": 180,
        "grow_time": 40,
        "yield_min": 2,
        "yield_max": 5,
    },
}

# Fruit trees (longer grow time, more yield)
FRUITS = {
    "apple": {
        "emoji": "🍎",
        "seed_cost": 1_200,
        "sell_price": 350,
        "grow_time": 120,  # 2 hours
        "yield_min": 4,
        "yield_max": 6,
    },
    "orange": {
        "emoji": "🍊",
        "seed_cost": 1_200,
        "sell_price": 350,
        "grow_time": 120,
        "yield_min": 4,
        "yield_max": 6,
    },
    "mango": {
        "emoji": "🥭",
        "seed_cost": 2_000,
        "sell_price": 600,
        "grow_time": 180,  # 3 hours
        "yield_min": 3,
        "yield_max": 5,
    },
    "watermelon": {
        "emoji": "🍉",
        "seed_cost": 1_500,
        "sell_price": 500,
        "grow_time": 150,
        "yield_min": 2,
        "yield_max": 4,
    },
    "strawberry": {
        "emoji": "🍓",
        "seed_cost": 1_000,
        "sell_price": 400,
        "grow_time": 90,
        "yield_min": 4,
        "yield_max": 8,
    },
    "grapes": {
        "emoji": "🍇",
        "seed_cost": 1_100,
        "sell_price": 380,
        "grow_time": 100,
        "yield_min": 5,
        "yield_max": 10,
    },
    "cherry": {
        "emoji": "🍒",
        "seed_cost": 1_000,
        "sell_price": 400,
        "grow_time": 100,
        "yield_min": 6,
        "yield_max": 10,
    },
    "peach": {
        "emoji": "🍑",
        "seed_cost": 1_500,
        "sell_price": 450,
        "grow_time": 130,
        "yield_min": 3,
        "yield_max": 5,
    },
    "lemon": {
        "emoji": "🍋",
        "seed_cost": 1_200,
        "sell_price": 380,
        "grow_time": 110,
        "yield_min": 4,
        "yield_max": 7,
    },
    "coconut": {
        "emoji": "🥥",
        "seed_cost": 2_500,
        "sell_price": 800,
        "grow_time": 240,  # 4 hours
        "yield_min": 2,
        "yield_max": 3,
    },
    "banana": {
        "emoji": "🍌",
        "seed_cost": 1_000,
        "sell_price": 320,
        "grow_time": 90,  # 1.5 hours
        "yield_min": 4,
        "yield_max": 8,
    },
    "pomegranate": {
        "emoji": "🍈",
        "custom_emoji_id": "5318803296532578717",
        "seed_cost": 2_500,
        "sell_price": 800,
        "grow_time": 200,  # 3h 20m
        "yield_min": 2,
        "yield_max": 4,
    },
}

# ============ FLOWERS ============

# Growable flowers — lower sell price, used mainly as crafting ingredients.
FLOWERS = {
    "rose": {
        "emoji": "🌹",
        "seed_cost": 600,
        "sell_price": 180,
        "grow_time": 20,
        "yield_min": 3,
        "yield_max": 6,
    },
    "tulip": {
        "emoji": "🌷",
        "seed_cost": 500,
        "sell_price": 150,
        "grow_time": 18,
        "yield_min": 3,
        "yield_max": 6,
    },
    "sunflower": {
        "emoji": "🌻",
        "seed_cost": 700,
        "sell_price": 200,
        "grow_time": 25,
        "yield_min": 2,
        "yield_max": 5,
    },
    "hibiscus": {
        "emoji": "🌺",
        "seed_cost": 800,
        "sell_price": 250,
        "grow_time": 30,
        "yield_min": 2,
        "yield_max": 4,
    },
    "lotus": {
        "emoji": "🪷",
        "seed_cost": 1_200,
        "sell_price": 350,
        "grow_time": 45,
        "yield_min": 2,
        "yield_max": 4,
    },
    "daisy": {
        "emoji": "🌼",
        "seed_cost": 400,
        "sell_price": 120,
        "grow_time": 15,
        "yield_min": 4,
        "yield_max": 7,
    },
    "cherry_blossom": {
        "emoji": "🌸",
        "seed_cost": 900,
        "sell_price": 280,
        "grow_time": 35,
        "yield_min": 2,
        "yield_max": 5,
    },
}

# Combined all plantable items
ALL_PLANTABLE = {**CROPS, **FRUITS, **FLOWERS}

# 25% commission on tomato_soup sales goes to this user, funded by the bot (not deducted from seller)
TOMATO_SOUP_COMMISSION_RATE = 0.25

TOMATO_SOUP_COMMISSION_USER_ID = os.environ.get(
    "TOMATO_SOUP_COMMISSION_USER_ID"
)


# ============ COOKING MACHINES ============

# Each machine has multiple recipes. Ingredients are auto-typed:
# items in FOODS are consumed as "food", all others as "harvest".
MACHINES = {
    "popcorn_machine": {
        "emoji": "🍿",
        "name": "Popcorn Machine",
        "cost": 25_000,
        "recipes": [
            {
                "name": "Popcorn",
                "produces": "popcorn",
                "emoji": "🍿",
                "sell_price": 600,
                "feed_value": 15,
                "ingredients": {"corn": 2},
            },
            {
                "name": "Caramel Popcorn",
                "produces": "caramel_popcorn",
                "emoji": "🍬",
                "sell_price": 2_500,
                "feed_value": 25,
                "ingredients": {"corn": 3, "apple": 1},
            },
            {
                "name": "Honeyed Popcorn",
                "produces": "honeyed_popcorn",
                "emoji": "🍯",
                "sell_price": 2_500,
                "feed_value": 25,
                "ingredients": {"corn": 3, "honey": 1},
            },
        ],
    },
    "fryer": {
        "emoji": "🍟",
        "name": "Deep Fryer",
        "cost": 35_000,
        "recipes": [
            {
                "name": "Fries",
                "produces": "fries",
                "emoji": "🍟",
                "sell_price": 800,
                "feed_value": 20,
                "ingredients": {"potato": 2},
            },
            {
                "name": "Tempura",
                "produces": "tempura",
                "emoji": "🍤",
                "sell_price": 4_000,
                "feed_value": 35,
                "ingredients": {"potato": 2, "carrot": 2, "broccoli": 1},
            },
        ],
    },
    "grill": {
        "emoji": "🍔",
        "name": "Grill",
        "cost": 50_000,
        "recipes": [
            {
                "name": "Burger",
                "produces": "burger",
                "emoji": "🍔",
                "sell_price": 1_500,
                "feed_value": 35,
                "ingredients": {"tomato": 1, "lettuce": 1, "onion": 1},
            },
            {
                "name": "BBQ Platter",
                "produces": "bbq_platter",
                "emoji": "🍖",
                "sell_price": 9_000,
                "feed_value": 55,
                "ingredients": {
                    "tomato": 2,
                    "pepper": 2,
                    "garlic": 1,
                    "eggplant": 1,
                },
            },
            {
                "name": "Nin's Tomato Soup",
                "produces": "tomato_soup",
                "emoji": "🍲",
                "custom_emoji_id": "6120740885459116296",
                "sell_price": 3_000,
                "feed_value": 40,
                "ingredients": {"tomato": 3, "onion": 1, "garlic": 1},
            },
            {
                "name": "Fried Egg",
                "produces": "fried_egg",
                "emoji": "🍳",
                "sell_price": 500,
                "feed_value": 15,
                "ingredients": {"egg": 2},
            },
            {
                "name": "Omelette",
                "produces": "omelette",
                "emoji": "🍳",
                "sell_price": 1_500,
                "feed_value": 30,
                "ingredients": {"egg": 3, "milk": 1},
            },
        ],
    },
    "juicer": {
        "emoji": "🧃",
        "name": "Juicer",
        "cost": 30_000,
        "recipes": [
            {
                "name": "Juice",
                "produces": "juice",
                "emoji": "🧃",
                "sell_price": 1_000,
                "feed_value": 25,
                "ingredients": {"apple": 2, "orange": 1},
            },
            {
                "name": "Pomegranate Juice",
                "produces": "pomegranate_juice",
                "emoji": "🍷",
                "sell_price": 12_000,
                "feed_value": 60,
                "ingredients": {"pomegranate": 3, "lemon": 1},
            },
            {
                "name": "Tropical Juice",
                "produces": "tropical_juice",
                "emoji": "🥝",
                "sell_price": 18_000,
                "feed_value": 70,
                "ingredients": {"mango": 2, "coconut": 1, "orange": 1},
            },
        ],
    },
    "oven": {
        "emoji": "🥧",
        "name": "Oven",
        "cost": 40_000,
        "recipes": [
            {
                "name": "Pie",
                "produces": "pie",
                "emoji": "🥧",
                "sell_price": 2_000,
                "feed_value": 40,
                "ingredients": {"apple": 3, "strawberry": 2},
            },
            {
                "name": "Fruit Cake",
                "produces": "fruit_cake",
                "emoji": "🎂",
                "sell_price": 60_000,
                "feed_value": 80,
                "ingredients": {"pie": 2, "cherry": 3, "peach": 2},
            },
            {
                "name": "Cream",
                "produces": "cream",
                "emoji": "🥛",
                "sell_price": 800,
                "feed_value": 0,
                "ingredients": {"milk": 2},
            },
            {
                "name": "Mashed Potato",
                "produces": "mashed_potato",
                "emoji": "🥔",
                "sell_price": 3_500,
                "feed_value": 45,
                "ingredients": {"potato": 2, "cream": 1, "butter": 1},
            },
            {
                "name": "Egg Tart",
                "produces": "egg_tart",
                "emoji": "🥮",
                "sell_price": 2_000,
                "feed_value": 35,
                "ingredients": {"egg": 3, "butter": 1},
            },
            {
                "name": "Honey Cake",
                "produces": "honey_cake",
                "emoji": "🍰",
                "sell_price": 4_000,
                "feed_value": 50,
                "ingredients": {"egg": 2, "honey": 2},
            },
            {
                "name": "Banana Bread",
                "produces": "banana_bread",
                "emoji": "🍞",
                "sell_price": 3_500,
                "feed_value": 50,
                "ingredients": {"banana": 3, "egg": 1, "butter": 1},
            },
        ],
    },
    "blender": {
        "emoji": "🥤",
        "name": "Blender",
        "cost": 20_000,
        "recipes": [
            {
                "name": "Smoothie",
                "produces": "smoothie",
                "emoji": "🥤",
                "sell_price": 1_200,
                "feed_value": 30,
                "ingredients": {"strawberry": 2, "mango": 1},
            },
            {
                "name": "Superfood Smoothie",
                "produces": "superfood_smoothie",
                "emoji": "🥤",
                "sell_price": 6_000,
                "feed_value": 50,
                "ingredients": {"strawberry": 3, "mango": 2, "coconut": 1},
            },
            {
                "name": "Butter",
                "produces": "butter",
                "emoji": "🧈",
                "sell_price": 1_000,
                "feed_value": 0,
                "ingredients": {"milk": 2},
            },
            {
                "name": "Cheese",
                "produces": "cheese",
                "emoji": "🧀",
                "sell_price": 2_500,
                "feed_value": 30,
                "ingredients": {"milk": 4},
            },
            {
                "name": "Yogurt",
                "produces": "yogurt",
                "emoji": "🫙",
                "sell_price": 1_800,
                "feed_value": 25,
                "ingredients": {"milk": 2, "honey": 1},
            },
            {
                "name": "Goat Cheese",
                "produces": "goat_cheese",
                "emoji": "🧀",
                "sell_price": 3_000,
                "feed_value": 35,
                "ingredients": {"goat_milk": 4},
            },
            {
                "name": "Banana Smoothie",
                "produces": "banana_smoothie",
                "emoji": "🍌",
                "sell_price": 2_800,
                "feed_value": 35,
                "ingredients": {"banana": 2, "milk": 1},
            },
            {
                "name": "Banana Milkshake",
                "produces": "banana_milkshake",
                "emoji": "🥛",
                "sell_price": 5_500,
                "feed_value": 45,
                "ingredients": {"banana": 3, "milk": 2, "honey": 1},
            },
        ],
    },
    "salad_maker": {
        "emoji": "🥗",
        "name": "Salad Maker",
        "cost": 17_500,
        "recipes": [
            {
                "name": "Salad",
                "produces": "salad",
                "emoji": "🥗",
                "sell_price": 700,
                "feed_value": 20,
                "ingredients": {"lettuce": 2, "tomato": 1, "carrot": 1},
            },
            {
                "name": "Caesar Salad",
                "produces": "caesar_salad",
                "emoji": "🥗",
                "sell_price": 3_500,
                "feed_value": 40,
                "ingredients": {"lettuce": 3, "tomato": 2, "garlic": 1},
            },
            {
                "name": "Egg Salad",
                "produces": "egg_salad",
                "emoji": "🥗",
                "sell_price": 1_000,
                "feed_value": 25,
                "ingredients": {"egg": 2, "lettuce": 1},
            },
        ],
    },
    "pizza_oven": {
        "emoji": "🍕",
        "name": "Pizza Oven",
        "cost": 75_000,
        "recipes": [
            {
                "name": "Pizza",
                "produces": "pizza",
                "emoji": "🍕",
                "sell_price": 3_000,
                "feed_value": 50,
                "ingredients": {
                    "tomato": 2,
                    "pepper": 1,
                    "onion": 1,
                    "garlic": 1,
                },
            },
            {
                "name": "Royal Feast",
                "produces": "royal_feast",
                "emoji": "🍱",
                "sell_price": 90_000,
                "feed_value": 100,
                "ingredients": {"pizza": 2, "burger": 2, "fries": 2},
            },
            {
                "name": "Grand Banquet",
                "produces": "grand_banquet",
                "emoji": "👑",
                "sell_price": 220_000,
                "feed_value": 100,
                "ingredients": {
                    "royal_feast": 1,
                    "fruit_cake": 1,
                    "pomegranate_juice": 2,
                },
            },
        ],
    },
    "confectionery": {
        "emoji": "🎂",
        "name": "Confectionery",
        "cost": 65_000,
        "recipes": [
            {
                "name": "Hot Cocoa",
                "produces": "hot_cocoa",
                "emoji": "☕",
                "sell_price": 2_000,
                "feed_value": 30,
                "ingredients": {"cocoa_beans": 2},
            },
            {
                "name": "Chocolate",
                "produces": "chocolate",
                "emoji": "🍫",
                "sell_price": 5_000,
                "feed_value": 45,
                "ingredients": {"cocoa_beans": 4},
            },
            {
                "name": "Chocolate Cake",
                "produces": "chocolate_cake",
                "emoji": "🎂",
                "custom_emoji_id": "5217603271354497221",
                "sell_price": 25_000,
                "feed_value": 75,
                "ingredients": {"chocolate": 2},
            },
            {
                "name": "Cocoa Truffle",
                "produces": "cocoa_truffle",
                "emoji": "🍫",
                "custom_emoji_id": "5309818907849153228",
                "sell_price": 18_000,
                "feed_value": 60,
                "ingredients": {"chocolate": 2, "cherry": 1},
            },
            {
                "name": "Choc Fondue",
                "produces": "choc_fondue",
                "emoji": "🫕",
                "sell_price": 9_000,
                "feed_value": 55,
                "ingredients": {"chocolate": 1, "strawberry": 2},
            },
            {
                "name": "Choco-Mango Delight",
                "produces": "choco_mango_delight",
                "emoji": "💝🥭",
                "sell_price": 15_000,
                "feed_value": 65,
                "ingredients": {"chocolate": 1, "mango": 2},
            },
        ],
    },
    "flower_workshop": {
        "emoji": "🌸",
        "name": "Flower Workshop",
        "cost": 30_000,
        "recipes": [
            {
                "name": "Flower Bouquet",
                "produces": "flower_bouquet",
                "emoji": "💐",
                "sell_price": 3_500,
                "feed_value": 0,
                "ingredients": {"rose": 2, "tulip": 2, "daisy": 3},
            },
            {
                "name": "Floral Hat",
                "produces": "floral_hat",
                "emoji": "👒",
                "sell_price": 22_000,
                "feed_value": 0,
                "ingredients": {"flower_bouquet": 2, "hibiscus": 3},
            },
            {
                "name": "Rose Perfume",
                "produces": "rose_perfume",
                "emoji": "🌹",
                "sell_price": 8_000,
                "feed_value": 0,
                "ingredients": {"rose": 5, "cherry_blossom": 2},
            },
            {
                "name": "Lotus Crown",
                "produces": "lotus_crown",
                "emoji": "🪷",
                "sell_price": 14_000,
                "feed_value": 0,
                "ingredients": {"lotus": 3, "cherry_blossom": 3},
            },
        ],
    },
}


# ============ COOKED FOOD ============

# Derived from MACHINES — do not edit here, edit the recipe in MACHINES above.
# Keys are the "produces" value; each entry contains emoji/sell_price/feed_value
# (and optional tg_emoji/custom_emoji_id) copied from the recipe that made it.
_RECIPE_FOOD_KEYS = {"produces", "ingredients"}

# AFTER:
_RECIPE_FOOD_KEYS = {"produces", "ingredients"}


def _build_food_entry(recipe: dict) -> dict:
    """Build a FOODS entry from a recipe, auto-deriving tg_emoji from
    custom_emoji_id if tg_emoji is not explicitly provided."""
    entry = {k: v for k, v in recipe.items() if k not in _RECIPE_FOOD_KEYS}
    if "custom_emoji_id" in entry and "tg_emoji" not in entry:
        cid = entry["custom_emoji_id"]
        emoji = entry["emoji"]
        entry["tg_emoji"] = f'<tg-emoji emoji-id="{cid}">{emoji}</tg-emoji>'
    return entry


FOODS: dict = {
    recipe["produces"]: _build_food_entry(recipe)
    for machine in MACHINES.values()
    for recipe in machine["recipes"]
}


# ============ SHOP CATEGORIES ============

SHOP_CATEGORIES = {
    "seeds": "🌱 Seeds & Saplings",
    "machines": "⚙️ Machines",
    "animals": "🐄 Animal Pens",
    "marketplace": "🏪 Marketplace",
    "essentials": "🛒 Essentials",
}


# ============ ANIMAL FARMING ============

ANIMAL_PENS = {
    "chicken_coop": {
        "emoji": "🐔",
        "name": "Chicken Coop",
        "animal_type": "chicken",
        "base_capacity": 5,
        "cost": 5_000,
        "upgrade_costs": {2: 10_000, 3: 20_000},
    },
    "cow_pasture": {
        "emoji": "🐄",
        "name": "Cow Pasture",
        "animal_type": "cow",
        "base_capacity": 5,
        "cost": 15_000,
        "upgrade_costs": {2: 30_000, 3: 60_000},
    },
    "goat_pen": {
        "emoji": "🐐",
        "name": "Goat Pen",
        "animal_type": "goat",
        "base_capacity": 5,
        "cost": 10_000,
        "upgrade_costs": {2: 20_000, 3: 40_000},
    },
    "sheep_pen": {
        "emoji": "🐑",
        "name": "Sheep Pen",
        "animal_type": "sheep",
        "base_capacity": 5,
        "cost": 8_000,
        "upgrade_costs": {2: 16_000, 3: 32_000},
    },
    "beehive": {
        "emoji": "🐝",
        "name": "Beehive",
        "animal_type": "bee",
        "base_capacity": 5,
        "cost": 12_000,
        "upgrade_costs": {2: 24_000, 3: 48_000},
    },
}

ANIMALS = {
    "chicken": {
        "emoji": "🐔",
        "name": "Chicken",
        "pen_type": "chicken_coop",
        "feed_type": "chicken_feed",
        "produce": "egg",
        "produce_emoji": "🥚",
        "produce_min": 1,
        "produce_max": 3,
        "produce_time": 1800,  # seconds
        "cost": 500,
    },
    "cow": {
        "emoji": "🐄",
        "name": "Cow",
        "pen_type": "cow_pasture",
        "feed_type": "cow_feed",
        "produce": "milk",
        "produce_emoji": "🥛",
        "produce_min": 1,
        "produce_max": 3,
        "produce_time": 1800,
        "cost": 2_000,
    },
    "goat": {
        "emoji": "🐐",
        "name": "Goat",
        "pen_type": "goat_pen",
        "feed_type": "goat_feed",
        "produce": "goat_milk",
        "produce_emoji": "🥛",
        "produce_min": 1,
        "produce_max": 3,
        "produce_time": 1800,
        "cost": 1_500,
    },
    "sheep": {
        "emoji": "🐑",
        "name": "Sheep",
        "pen_type": "sheep_pen",
        "feed_type": "sheep_feed",
        "produce": "wool",
        "produce_emoji": "🧶",
        "produce_min": 1,
        "produce_max": 2,
        "produce_time": 1800,
        "cost": 1_200,
    },
    "bee": {
        "emoji": "🐝",
        "name": "Bee",
        "pen_type": "beehive",
        "feed_type": None,
        "produce": "honey",
        "produce_emoji": "🍯",
        "produce_min": 1,
        "produce_max": 2,
        "produce_time": 1800,
        "cost": 800,
    },
}

ANIMAL_FEEDS = {
    "chicken_feed": {
        "emoji": "🌾",
        "name": "Chicken Feed",
        "ingredients": {"corn": 1, "wheat": 2},
    },
    "cow_feed": {
        "emoji": "🌿",
        "name": "Cow Feed",
        "ingredients": {"corn": 1, "soybean": 2},
    },
    "goat_feed": {
        "emoji": "🌿",
        "name": "Goat Feed",
        "ingredients": {"carrot": 2, "corn": 1, "wheat": 1},
    },
    "sheep_feed": {
        "emoji": "🌿",
        "name": "Sheep Feed",
        "ingredients": {"wheat": 2, "carrot": 1},
    },
}

# Stored in inventory with item_type="animal_produce"
ANIMAL_PRODUCE = {
    "egg": {"emoji": "🥚", "name": "Egg", "sell_price": 300},
    "milk": {"emoji": "🥛", "name": "Milk", "sell_price": 400},
    "goat_milk": {"emoji": "🥛", "name": "Goat Milk", "sell_price": 450},
    "wool": {"emoji": "🧶", "name": "Wool", "sell_price": 500},
    "honey": {"emoji": "🍯", "name": "Honey", "sell_price": 600},
}


# ============ HELPER FUNCTIONS ============


async def maybe_pay_tomato_soup_commission(
    db, item_name: str, total_price: int
) -> int:
    """Pay 25% commission to Tomato Soup Owner on any tomato_soup sale directly into their bank.
    Returns the commission amount paid (0 if not applicable)."""
    if TOMATO_SOUP_COMMISSION_USER_ID is None:
        return 0
    if item_name != "tomato_soup":
        return 0
    commission = int(total_price * TOMATO_SOUP_COMMISSION_RATE)
    if commission > 0:
        await db.add_bank_balance(TOMATO_SOUP_COMMISSION_USER_ID, commission)
    return commission


def get_crop_emoji(crop_name: str) -> str:
    """Get plain emoji for a crop/fruit/food (safe for button labels)."""
    for src in (CROPS, FRUITS, FLOWERS, FOODS, ANIMAL_PRODUCE, ANIMAL_FEEDS):
        if crop_name in src:
            return src[crop_name]["emoji"]
    return "❓"


def get_crop_display_emoji(crop_name: str) -> str:
    """Get display emoji for HTML message contexts.

    Returns the tg-emoji tag if one is defined (e.g. pomegranate), else
    the plain emoji.  Do NOT use this in InlineKeyboardButton labels —
    button text is plain-text only.
    """
    for src in (CROPS, FRUITS, FLOWERS, FOODS, ANIMAL_PRODUCE, ANIMAL_FEEDS):
        if crop_name in src:
            info = src[crop_name]
            return info.get("tg_emoji", info["emoji"])
    return "❓"


def get_ingredient_type(ingredient_name: str) -> str:
    """Return the inventory item_type string for a given ingredient."""
    if ingredient_name in FOODS:
        return "food"
    if ingredient_name in ANIMAL_PRODUCE:
        return "animal_produce"
    if ingredient_name in ANIMAL_FEEDS:
        return "feed"
    return "harvest"


def get_custom_emoji_id(item_name: str) -> Optional[str]:
    """Return custom_emoji_id string if item has a Telegram custom emoji, else None.

    Use this with InlineKeyboardButton(icon_custom_emoji_id=...) for proper
    rendering of custom-emoji items in button labels.
    """
    for src in (CROPS, FRUITS, FLOWERS, FOODS, ANIMAL_PRODUCE, ANIMAL_FEEDS):
        if item_name in src:
            return src[item_name].get("custom_emoji_id")
    return None


def format_item_name(name: str) -> str:
    """Format an internal item key for display (caramel_popcorn → Caramel Popcorn)."""
    return name.replace("_", " ").title()


def get_item_display_name(item_key: str) -> str:
    """Return the human-readable name for any item key.

    Prefers the recipe 'name' field (e.g. "Nin's Tomato Soup") over the
    auto-formatted key (e.g. "Tomato Soup").  Falls back to format_item_name
    for crops, fruits, flowers, and anything without an explicit name.
    """
    if item_key in FOODS and "name" in FOODS[item_key]:
        return FOODS[item_key]["name"]
    for src in (CROPS, FRUITS, FLOWERS, ANIMAL_PRODUCE, ANIMAL_FEEDS):
        if item_key in src and "name" in src[item_key]:
            return src[item_key]["name"]
    return format_item_name(item_key)


def parse_item_and_qty(args: list[str], all_value: int = 1) -> tuple[str, int]:
    """Parse item name and optional quantity from command argument tokens.

    Supports both "<item> <qty>" and "<qty> <item>" order. If the first token
    is a digit or "a"/"all", it's treated as the quantity and the remaining
    tokens become the item name. Otherwise it scans left-to-right: the first
    digit-only token is the quantity; "a"/"all" returns `all_value` (pass
    999999 for plant/sell commands); every token before it is joined as the
    item name; everything after is ignored.

    Examples:
      ["rose_perfume", "50", "will", "you", "marry", "me"] → ("rose_perfume", 50)
      ["cherry", "blossom", "9000"] → ("cherry blossom", 9000)
      ["1000", "flower", "bouquet"] → ("flower bouquet", 1000)
      ["cocoa", "5"]               → ("cocoa", 5)
      ["cocoa_beans"]              → ("cocoa_beans", 1)
      ["carrot", "all"]            → ("carrot", all_value)
    """
    if not args:
        return "", 1
    first = args[0].lower()
    if args[0].isdigit() or first in ("a", "all"):
        qty = int(args[0]) if args[0].isdigit() else all_value
        return " ".join(args[1:]), qty

    name_parts: list[str] = []
    qty = 1
    for part in args:
        if part.isdigit():
            qty = int(part)
            break
        if part.lower() in ("a", "all"):
            qty = all_value
            break
        name_parts.append(part)
    return " ".join(name_parts), qty


def resolve_item_key(user_input: str) -> Optional[str]:
    """Resolve user-typed item name to an internal key.

    Handles spaces ("cocoa beans"), underscores ("cocoa_beans"),
    optional trailing-s plural ("cocoa bean" → "cocoa_beans"),
    and prefix matching as a last resort ("coc" → "cocoa_beans").
    """
    normalized = user_input.strip().lower().replace(" ", "_").replace("-", "_")
    if not normalized:
        return None
    all_keys = {
        **CROPS,
        **FRUITS,
        **FLOWERS,
        **FOODS,
        **ANIMAL_PRODUCE,
        **ANIMAL_FEEDS,
    }

    if normalized in all_keys:
        return normalized

    # Strip or add trailing 's' for optional plural
    if normalized.endswith("s") and normalized[:-1] in all_keys:
        return normalized[:-1]
    if normalized + "s" in all_keys:
        return normalized + "s"

    # Prefix match — return shortest matching key
    matches = [k for k in all_keys if k.startswith(normalized)]
    if matches:
        return min(matches, key=len)

    return None


def get_all_sellable() -> Dict:
    """Get all items that can be sold."""
    sellable = {}
    for name, data in {
        **CROPS,
        **FRUITS,
        **FLOWERS,
        **FOODS,
        **ANIMAL_PRODUCE,
    }.items():
        sellable[name] = {
            "emoji": data["emoji"],
            "sell_price": data["sell_price"],
        }
    return sellable


def format_time(minutes: int) -> str:
    """Format minutes into human readable time."""
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    if mins == 0:
        return f"{hours}h"
    return f"{hours}h {mins}m"


def format_price(amount: int) -> str:
    """Format price with currency symbol."""
    return f"{CURRENCY_SYMBOL}{amount:,}"


def get_worker_level(xp: int) -> int:
    """Get worker level from XP."""
    level = 1
    for lvl, required_xp in sorted(WORKER_LEVEL_XP.items()):
        if xp >= required_xp:
            level = lvl
        else:
            break
    return level


def get_worker_max_factories(xp: int) -> int:
    """Get how many factories a worker can work at based on their XP."""
    level = get_worker_level(xp)
    return WORKER_FACTORY_SLOTS.get(level, 2)


def calculate_garden_fertilize_bonus(garden_size: int) -> int:
    """
    Fertilize fully grows every growing crop in the target's garden.
    Returns: reward_per_plant
    """
    return FERTILIZE_REWARD_PER_PLANT


# ============ SONAR GAME CONSTANTS ============

SONAR_GRID_SIZE = 10
SONAR_NUM_CHESTS = 5
SONAR_CHEST_COUNT = SONAR_NUM_CHESTS
SONAR_GUESS_COST = 150
SONAR_CHEST_REWARD = 500


# ============ FISHING CONSTANTS ============

BAIT_COST = 20
STARTING_BAIT = 5

# Fish types with rarity (weight) and sell price
# Higher weight = more common. Rare fish heavily nerfed to prevent money farming.
FISH_TYPES = {
    "boot": {
        "emoji": "👞",
        "name": "Old Boot",
        "rarity": 70,
        "sell_price": 1,
    },
    "sardine": {
        "emoji": "🐟",
        "name": "Sardine",
        "rarity": 35,
        "sell_price": 10,
    },
    "tropical": {
        "emoji": "🐠",
        "name": "Tropical Fish",
        "rarity": 25,
        "sell_price": 30,
    },
    "pufferfish": {
        "emoji": "🐡",
        "name": "Pufferfish",
        "rarity": 10,
        "sell_price": 75,
    },
    "shark": {
        "emoji": "🦈",
        "name": "Shark",
        "rarity": 3,
        "sell_price": 200,
    },
    "whale": {
        "emoji": "🐋",
        "name": "Whale",
        "rarity": 1,
        "sell_price": 500,
    },
    "kraken": {
        "emoji": "🦑",
        "name": "Kraken",
        "rarity": 0.3,
        "sell_price": 1000,
    },
    "mermaid": {
        "emoji": "🧜",
        "name": "Mermaid",
        "rarity": 0.1,
        "sell_price": 5000,
    },
}


# ============ JOBS CONSTANTS ============

JOB_TYPES = {
    "police": {
        "emoji": "👮",
        "name": "Police Officer",
        "description": "Investigate and arrest criminals",
        "work_cooldown": 30 * 60,  # 30 minutes
        "min_reward": 20_000,
        "max_reward": 30_000,
    },
    "thief": {
        "emoji": "🦹",
        "name": "Thief",
        "description": "Steal from houses and players",
        "work_cooldown": 45 * 60,  # 45 minutes
        "min_reward": 30_000,
        "max_reward": 55_000,
    },
    "gangster": {
        "emoji": "🔫",
        "name": "Gangster",
        "description": "Control territory and extort",
        "work_cooldown": 60 * 60,  # 1 hour
        "min_reward": 25_000,
        "max_reward": 35_000,
    },
    "doctor": {
        "emoji": "👨‍⚕️",
        "name": "Doctor",
        "description": "Heal injured players",
        "work_cooldown": 20 * 60,  # 20 minutes
        "min_reward": 60_000,
        "max_reward": 80_000,
    },
}

JOB_XP_PER_WORK = 25


# ─────────────── XP / level formula ───────────────
# XP required to go from level `x` to level `x+1`.
#     y = 2.095·x² + 53.37·x + 187
# So level-1 needs ~242 XP to hit level-2, level-2 needs ~302 XP to hit
# level-3, etc. Cumulative XP to reach level L = sum of xp_for_next_level
# over x = 1..L-1.


def xp_for_next_level(current_level: int) -> int:
    """XP needed to go from current_level → current_level + 1."""
    x = max(1, int(current_level))
    return int(round(2.095 * x * x + 53.37 * x + 187.0))


def get_xp_for_level(level: int) -> int:
    """Cumulative XP needed to *reach* the given level (level 1 = 0)."""
    if level <= 1:
        return 0
    total = 0
    for lvl in range(1, level):
        total += xp_for_next_level(lvl)
    return total


def get_job_level(xp: int) -> int:
    """Reverse: total XP → current level. No cap, requirements scale."""
    if xp <= 0:
        return 1
    level = 1
    cumulative = 0
    while True:
        need = xp_for_next_level(level)
        if xp >= cumulative + need:
            cumulative += need
            level += 1
        else:
            return level


# ─────────────── Action daily-limit formula ───────────────
# Each combat/crime action's daily limit scales with the relevant job's
# skill level using:  limit(action, lvl) = ACTION_LIMIT_BASE[action] + floor(sqrt(lvl)).
# Designed so /arrest hits 3 @ L1, 4 @ L5, 5 @ L10, 6 @ L17, 7 @ L25 — and
# the other actions follow the same shape with their own bases.


ACTION_LIMIT_BASE = {
    "arrest": 2,  # base 2 → +1 @ L1 = 3,  +5 @ L25 = 7
    "kill": 1,  # gangster skill
    "rob": 1,  # thief skill
    "heal": 0,  # doctor skill — 1 @ L1, 2 @ L4, 3 @ L9, 4 @ L16, 5 @ L25
    "heist": 0,  # thief skill — same shape as heal
}

# Which job-type's level drives each action's limit.
ACTION_SKILL_JOB = {
    "arrest": "police",
    "kill": "gangster",
    "rob": "thief",
    "heal": "doctor",
    "heist": "thief",
}


def action_limit_for_level(action: str, level: int) -> int:
    """How many `action` plays per day at the given skill level."""
    base = ACTION_LIMIT_BASE.get(action, 0)
    lvl = max(1, int(level))
    return base + int(_math.isqrt(lvl))


def level_for_action_limit(action: str, limit: int) -> int:
    """The smallest level at which the daily limit hits `limit`."""
    base = ACTION_LIMIT_BASE.get(action, 0)
    needed_sqrt = max(0, int(limit) - base)
    return max(1, needed_sqrt * needed_sqrt)


# ─────────────── Action XP ranges ───────────────
# XP gained from a single action attempt, scaling with skill level. Win
# gives more, loss still gives some (failure rewards perseverance). Ranges
# are inclusive on both ends and picked with random.randint at call-site.


def get_action_xp_range(level: int, won: bool) -> tuple[int, int]:
    """(min_xp, max_xp) for a single action attempt at given skill level."""
    lvl = max(1, int(level))
    if won:
        lo = 50 + (lvl - 1) * 7
        hi = 70 + (lvl - 1) * 12
    else:
        lo = 10 + (lvl - 1) * 2
        hi = 20 + (lvl - 1) * 4
    return lo, hi


# Compatibility constant kept for any old code that imports it.
JOB_LEVEL_XP = {
    1: 0,
    2: get_xp_for_level(2),
    3: get_xp_for_level(3),
    4: get_xp_for_level(4),
    5: get_xp_for_level(5),
}


# ============ ACHIEVEMENT DEFINITIONS ============

ACHIEVEMENTS = {
    # Family achievements
    "first_marriage": {
        "emoji": "💍",
        "name": "First Marriage",
        "description": "Get married for the first time",
    },
    "first_child": {
        "emoji": "👶",
        "name": "First Child",
        "description": "Adopt your first child",
    },
    "big_family": {
        "emoji": "👨‍👩‍👧‍👦",
        "name": "Big Family",
        "description": "Have 10 family members",
    },
    # Social achievements
    "friendly": {
        "emoji": "🤝",
        "name": "Friendly",
        "description": "Make 10 friends",
    },
    "social_butterfly": {
        "emoji": "🦋",
        "name": "Social Butterfly",
        "description": "Make 25 friends",
    },
    "popular": {
        "emoji": "🌟",
        "name": "Popular",
        "description": "Make 50 friends",
    },
    "influencer": {
        "emoji": "📣",
        "name": "Influencer",
        "description": "Make 70 friends",
    },
    "crowd_legend": {
        "emoji": "👑",
        "name": "Crowd Legend",
        "description": "Make 100 friends",
    },
    # Economy achievements
    "first_100k": {
        "emoji": "💰",
        "name": "First $100k",
        "description": "Earn $100,000 total",
    },
    "millionaire": {
        "emoji": "💎",
        "name": "Millionaire",
        "description": "Have $1,000,000 balance",
    },
    # Gambling achievements
    "lucky_winner": {
        "emoji": "🎰",
        "name": "Lucky Winner",
        "description": "Win 10 gambling games",
    },
    "high_roller": {
        "emoji": "🎲",
        "name": "High Roller",
        "description": "Win $50,000 from gambling",
    },
    # Factory achievements
    "factory_owner": {
        "emoji": "🏭",
        "name": "Factory Owner",
        "description": "Open your first factory",
    },
    "factory_tycoon": {
        "emoji": "🏗️",
        "name": "Factory Tycoon",
        "description": "Earn $100,000 from factory",
    },
    # Garden achievements
    "green_thumb": {
        "emoji": "🌱",
        "name": "Green Thumb",
        "description": "Harvest 100 crops",
    },
    "master_farmer": {
        "emoji": "🌾",
        "name": "Master Farmer",
        "description": "Expand garden to 10x10",
    },
    # Fishing achievements
    "first_catch": {
        "emoji": "🎣",
        "name": "First Catch",
        "description": "Catch your first fish",
    },
    "legendary_fisher": {
        "emoji": "🦑",
        "name": "Legendary Fisher",
        "description": "Catch a Kraken",
    },
    # Craft achievements
    "craft_myth_mon_master": {
        "emoji": "🌌",
        "name": "Myth & Monster Archivist",
        "description": "Discover all mythical and monster crafts",
    },
    "craft_official_master": {
        "emoji": "🏛️",
        "name": "Official Alchemy Master",
        "description": "Complete all 830 official crafts",
    },
    "craft_extras_500": {
        "emoji": "🧸",
        "name": "Extras Explorer I",
        "description": "Discover 500 EXTRAS crafts",
    },
    "craft_extras_1000": {
        "emoji": "🛍️",
        "name": "Extras Explorer II",
        "description": "Discover 1,000 EXTRAS crafts",
    },
    "craft_extras_1500": {
        "emoji": "🧸",
        "name": "Extras Explorer III",
        "description": "Discover 1,500 EXTRAS crafts",
    },
    "craft_extras_2000": {
        "emoji": "🛍️",
        "name": "Extras Explorer IV",
        "description": "Discover 2,000 EXTRAS crafts",
    },
    "craft_extras_2500": {
        "emoji": "🧸",
        "name": "Extras Explorer V",
        "description": "Discover 2,500 EXTRAS crafts",
    },
    "craft_extras_3000": {
        "emoji": "🛍️",
        "name": "Extras Explorer VI",
        "description": "Discover 3,000 EXTRAS crafts",
    },
    "craft_extras_master": {
        "emoji": "🎁",
        "name": "Extras Completionist",
        "description": "Discover all EXTRAS crafts",
    },
    "craft_prog_first": {
        "emoji": "💻",
        "name": "When You Discover Nerd Life",
        "description": "Discover your first programming craft",
    },
    "craft_prog_master": {
        "emoji": "🧠",
        "name": "Mastering Computer Language",
        "description": "Discover all programming crafts",
    },
    "craft_gaming_first": {
        "emoji": "🎮",
        "name": "Newbie Gamer",
        "description": "Discover your first gaming craft",
    },
    "craft_gaming_50": {
        "emoji": "🕹️",
        "name": "Rising Gamer",
        "description": "Discover 50 gaming crafts",
    },
    "craft_gaming_100": {
        "emoji": "🏆",
        "name": "Elite Gamer",
        "description": "Discover 100 gaming crafts",
    },
    "craft_gaming_master": {
        "emoji": "👑",
        "name": "Gamemaster",
        "description": "Discover all gaming crafts",
    },
    "craft_country_first": {
        "emoji": "🗺️",
        "name": "Globe Starter",
        "description": "Discover your first country craft",
    },
    "craft_country_50": {
        "emoji": "🌍",
        "name": "Country Collector I",
        "description": "Discover 50 country crafts",
    },
    "craft_country_100": {
        "emoji": "🌎",
        "name": "Country Collector II",
        "description": "Discover 100 country crafts",
    },
    "craft_country_150": {
        "emoji": "🌏",
        "name": "Country Collector III",
        "description": "Discover 150 country crafts",
    },
    "craft_country_master": {
        "emoji": "🎓",
        "name": "Geography Master",
        "description": "Discover all country crafts",
    },
    # Pet achievements
    "pet_lover": {
        "emoji": "🐾",
        "name": "Pet Lover",
        "description": "Raise your pet to Level 20",
    },
}


# ============ GANG SYSTEM CONSTANTS ============

GANG_CREATION_FEE = 20000  # $20k fees to create gang
GANGWAR_DAILY_LIMIT = 2  # Per user
GANGWAR_IMMUNITY_HOURS = 6  # Hours of immunity after gang war
GANGWAR_HEART_LOSS_PERCENT = 40  # 40% of losing gang loses hearts
GANGWAR_REWARD_BASE = 5000  # Base reward for winning gang war
GANGWAR_REWARD_PER_LEVEL = 1000  # Additional reward per level
GANG_LEAVE_COOLDOWN_UNTIL_UTC = True  # Can't join new gang until UTC reset


# ============ HEIST SYSTEM CONSTANTS ============

HEIST_STEAL_PERCENT = 0.05  # 5% of victim's bank
HEIST_STEAL_CAP = 10000000  # Max steal capped at 10M
HEIST_LOSE_PERCENT = 0.40  # 40% of stolen amount
HEIST_LOSE_CAP = 100000000  # Max loss capped at 100M
HEIST_FAIL_VAULT_PERCENT = 0.10  # Percentage of money in failed heist vaults
HEIST_DAILY_BASE = 1  # Base heists per day
HEIST_VAULTS_NORMAL = 3  # Number of vaults without security
HEIST_VAULTS_WITH_SECURITY = 5  # Number of vaults with security
HEIST_COOLDOWN_HOURS = 6  # Hours before can heist same target again


# ============ SECURITY SYSTEM CONSTANTS ============

SECURITY_PERCENTAGE = 0.0025  # 0.25% of net worth (wallet + bank)
SECURITY_NOTIFICATION_DM = True  # Notify user via DM when security breaks


# ============ PET SYSTEM ============

PET_MIN_LEVEL = 7
PET_LEVELUP_COST = 500_000
PET_MAX_LEVEL = 20
PET_MAX_HAPPINESS = 100


def get_next_pet_cost(owned_count: int) -> int:
    """Cost to acquire the next pet: 1M for the first, ×10 for each after."""
    return 1_000_000 * (10**owned_count)


PETS: dict = {
    "cat": {
        "emoji": "🐈‍⬛",
        "name": "Cat",
        "cost": 1_000_000,
        "sounds": {
            "feed": [
                "Purrrr~ 😸 *nom nom nom*",
                "Mrrrow~ 🐱 *sniffs it carefully then eats*",
                "Meow! 😺 *licks paw after eating*",
                "Purrr... 😻 *kneads the floor contentedly*",
            ],
            "pet": [
                "Meowww~~ 😸 *slow blinks at you*",
                "Purrrrr~ 🐱 *rubs head against your hand*",
                "Mrrrow~ 😺 *arches back and stretches*",
                "Prrrr-meow! 😻 *rolls onto back*",
            ],
            "play": [
                "Meoow! 🐱 *bats at toy furiously*",
                "Prrr-meow~ 😸 *chases tail in circles*",
                "MEOW! 😺 *leaps and pounces!*",
                "*chirp chirp* 🐱 *stares at invisible thing on the wall*",
            ],
        },
    },
    "dog": {
        "emoji": "🐶",
        "name": "Dog",
        "cost": 1_000_000,
        "sounds": {
            "feed": [
                "Woof! 🐶 *wags tail at mach speed*",
                "Arf arf! 😋 *scarfs it all down instantly*",
                "Bork bork! 🐕 Nom nom nom!",
                "*happy panting* 🐶 *licks bowl completely clean*",
            ],
            "pet": [
                "Woof woof~ 🐶 *licks your entire face*",
                "Arf! 🐕 *immediately rolls over for belly rubs*",
                "*tail wagging intensifies* 🐶 *wiggles whole body*",
                "Wroof~ 😊 *nuzzles deep into your hand*",
            ],
            "play": [
                "WOOF!! 🐶 *does zoomies around the room*",
                "Bark bark! 🐕 *fetches and drops it at your feet*",
                "Rowf rowf! 🐶 *spins in happy circles*",
                "Yip yip! 🐕 *leaps and bounces off the walls*",
            ],
        },
    },
    "horse": {
        "emoji": "🐴",
        "name": "Horse",
        "cost": 1_000_000,
        "sounds": {
            "feed": [
                "*soft neigh* 🐴 *munches contentedly*",
                "Neeigh~ 🐎 *nuzzles deep into feed bucket*",
                "*snorts happily* 🐴 *chomps the whole apple*",
                "Whinny~ 😊 *stamps hoof in approval*",
            ],
            "pet": [
                "Neeigh~ 🐴 *shakes mane proudly*",
                "*warm gentle snort* 🐎 *leans into the pat*",
                "Whinny whinny~ 🐴 *swishes tail*",
                "*stamps hoof slowly* 🐎 *nudges you with nose*",
            ],
            "play": [
                "NEEIGH!! 🐴 *gallops in big happy circles*",
                "*excited trotting* 🐎 *prances around showing off*",
                "Whinny! 🐴 *rears up playfully*",
                "*loud snorts* 🐎 *full zoomies mode activated*",
            ],
        },
    },
    "fox": {
        "emoji": "🦊",
        "name": "Fox",
        "cost": 1_000_000,
        "sounds": {
            "feed": [
                "Yip! 🦊 *grabs food and sprints away*",
                "*curious nose twitching* 🦊 *nibbles daintily*",
                "Yiiip~ 🦊 *secretly stashes some for later*",
                "*soft chittering* 🦊 Nom nom~",
            ],
            "pet": [
                "Yip yip~ 🦊 *flicks fluffy tail happily*",
                "*happy screech* 🦊 *rolls around on floor*",
                "Yiiip~ 🦊 *nuzzles your hand*",
                "*ear flicks* 🦊 *looks away smugly then immediately comes back*",
            ],
            "play": [
                "YIIIP!! 🦊 *pounces on absolutely everything*",
                "*fox screech* 🦊 *does impressive parkour*",
                "Yip yip yip! 🦊 *spins and cartwheels somehow*",
                "*dramatic screech* 🦊 *leaps off every surface*",
            ],
        },
    },
    "rabbit": {
        "emoji": "🐰",
        "name": "Rabbit",
        "cost": 1_000_000,
        "sounds": {
            "feed": [
                "*nose twitching rapidly* 🐰 *nibbles carrot*",
                "Squeak~ 🐇 *munches greens happily*",
                "*thump thump* 🐰 *devours everything*",
                "*binky jump first* 🐇 THEN nom nom",
            ],
            "pet": [
                "*BINKIES!* 🐰 *leaps for pure joy*",
                "Squeak squeak~ 🐇 *licks your finger*",
                "*nose twitching at max speed* 🐰 *flops over dramatically*",
                "*purring somehow* 🐇 yes rabbits actually do that~",
            ],
            "play": [
                "*MEGA BINKY* 🐰 *zooms at the speed of light*",
                "Thump thump! 🐇 *binkies keep intensifying*",
                "*head shake* 🐰 *zooms again and again*",
                "Squeak! 🐇 *digs a very enthusiastic imaginary hole*",
            ],
        },
    },
    "parrot": {
        "emoji": "🦜",
        "name": "Parrot",
        "cost": 1_000_000,
        "sounds": {
            "feed": [
                "SQUAWK! 🦜 *grabs food with foot like a hand*",
                "*loud crunching noises* 🦜 Polly want MORE!",
                "Awk awk! 🦜 *bobs head while eating*",
                "*wolf whistle* 🦜 This is delicious!!",
            ],
            "pet": [
                "SQUAAAWK! 🦜 *ruffles all feathers happily*",
                "*rapid beak clicking* 🦜 Pretty bird~ pretty bird~",
                "Awk! 🦜 *does a full little dance*",
                "*wolf whistle* 🦜 *head bobs non-stop*",
            ],
            "play": [
                "SQUAAAWK! 🦜 *flies in dramatic circles*",
                "Awk awk awk! 🦜 *mimics everything you say back at you*",
                "*full screech* 🦜 *hangs upside down from perch*",
                "SQUAWK!! 🦜 *spreads wings to maximum width*",
            ],
        },
    },
}

"""The ten vocabulary categories, and how the old nineteen topics map onto them.

Nineteen topics were too many and too uneven -- `communication` held five
words, `basics` fifty-four -- so picking one gave a pool too small to fill a
deck. Worse, `topic` could only ever hold one value, which forced arbitrary
choices: `å betale` is money and it is shopping, and the schema made that an
either/or (ADR-038).

Categories are now many-to-many. A word may sit in two, and progress counts
the item once wherever it appears, because learning lives on the item and not
on the pairing -- which is what makes overlap free rather than double-counted.
"""

CATEGORIES: dict[str, str] = {
    "essentials": "Everyday essentials",
    "people": "People & family",
    "home": "Home & daily life",
    "food": "Food & drink",
    "travel": "Travel & getting around",
    "work-school": "Work, school & money",
    "health": "Body & health",
    "time-numbers": "Time & numbers",
    "nature": "Nature & weather",
    "leisure": "Leisure & culture",
}

BLURBS: dict[str, str] = {
    "essentials": "Pronouns, question words, greetings and the verbs you need everywhere",
    "people": "Family, relationships and describing the people around you",
    "home": "The home, clothes, chores and the shape of an ordinary day",
    "food": "Eating, drinking, cooking and buying food",
    "travel": "Places, transport, directions and getting where you are going",
    "work-school": "Work, study, language learning and money",
    "health": "The body, feeling well and feeling unwell",
    "time-numbers": "Counting, clock time, days and how often things happen",
    "nature": "Weather, seasons, animals and the outdoors",
    "leisure": "Hobbies, sport, celebrations and Norwegian culture",
}

# Old topic slug -> the categories that topic's words belong in. Several map
# to two: "meeting people" is both the greetings themselves and the people
# being greeted, and splitting that was the kind of arbitrary choice the old
# single-valued column forced.
TOPIC_MAP: dict[str, tuple[str, ...]] = {
    "basics": ("essentials",),
    "everyday-verbs": ("essentials",),
    "describing": ("essentials",),
    "communication": ("essentials",),
    "meeting-people": ("essentials", "people"),
    "people-family": ("people",),
    "home": ("home",),
    "daily-routine": ("home",),
    "food-drink": ("food",),
    "places-travel": ("travel",),
    "directions": ("travel",),
    "shopping": ("work-school",),
    "language-school": ("work-school",),
    "body-health": ("health",),
    "time-numbers": ("time-numbers",),
    "nature-weather": ("nature",),
    "hobbies-leisure": ("leisure",),
    "celebrations": ("leisure",),
    "culture": ("leisure",),
}


def categories_for_topic(topic: str | None) -> tuple[str, ...]:
    """Where a legacy single topic lands. Unknown topics go to essentials."""
    if not topic:
        return ("essentials",)
    return TOPIC_MAP.get(topic, ("essentials",))


def label(slug: str) -> str:
    return CATEGORIES.get(slug, slug.replace("-", " ").capitalize())

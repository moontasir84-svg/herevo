import pytest

from app.safety import screen


@pytest.mark.parametrize("text", [
    "দেবর হুমকি দিচ্ছে আমাকে বাড়ি থেকে বের করে দেবে।",
    "আমার স্বামী আমাকে মারধর করে",
    "স্বামী প্রতিদিন মারছে",
    "My employer hits me and threatens me",
    "They want to throw me out of the house",
    "Mwajiri wangu ananipiga na kunitishia.",
    "Mi expareja me amenaza cuando voy al mercado.",
    "আমি খুব ভয় পাচ্ছি",
])
def test_escalates_threats(text):
    assert screen(text).escalate


@pytest.mark.parametrize("text", [
    "স্বামী মারা যাওয়ার পর দেবর বলছে জমি আমার না।",   # "died" (মারা) is not "hits" (মারে)
    "আমারে একটু বলেন ধানের পাতা হলুদ কেন",            # "to me" contains the letters of মারে
    "আজ ৪০ কেজি ধান বিক্রি করেছি, ১২০০ টাকা।",
    "My soil is acidic, what should I plant?",
    "How do I get a loan for my stall?",
    "I need a skill training course",
])
def test_does_not_escalate_ordinary_questions(text):
    assert not screen(text).escalate

import random

from regular.pet_props import PropPlay


def test_limit_lifetime_and_hit_order():
    play = PropPlay(random.Random(1))
    ids = [play.emit("ball", 50, 50, 0) for _ in range(6)]
    assert len(play.items) == 2
    assert play.hit(50, 50) == ids[-1]
    assert all(20 <= item.ttl <= 60 for item in play.items)
    play.tick(61, 0, 100, 0, 100)
    assert not play.items


def test_tap_drag_and_bounded_motion():
    play = PropPlay(random.Random(2))
    coin = play.emit("coin", 40, 40, 0)
    ball = play.emit("ball", 50, 50, 0)
    assert play.tap(coin, 0) == "coin"
    assert play.hit(40, 40) is None
    assert play.tap(ball, 0) == "ball"
    assert play.items[0].vy < 0
    play.drag(ball, 90, 90, 0)
    play.tick(0, 0, 100, 0, 100)
    assert (play.items[0].x, play.items[0].y) == (90, 90)
    play.release(ball, 0)
    play.tick(1, 0, 100, 0, 100)
    item = play.items[0]
    assert item.radius <= item.x <= 100 - item.radius
    assert item.radius <= item.y <= 100 - item.radius


def test_ground_toys_come_fully_to_rest_and_stay_still():
    play = PropPlay(random.Random(3))
    ball = play.emit("ball", 50, 30, 0)
    coin = play.emit("coin", 70, 30, 0)
    now = 0.0
    for _ in range(600):
        now += 1 / 30
        play.tick(now, 0, 200, 0, 100)
    assert {item.id for item in play.candidates(now)} == {ball, coin}
    before = [(item.x, item.y, item.vx, item.vy) for item in play.items]
    for _ in range(30):
        now += 1 / 30
        play.tick(now, 0, 200, 0, 100)
    assert [(item.x, item.y, item.vx, item.vy) for item in play.items] == before


def test_kick_and_collect_recheck_drag_lifetime_and_kind():
    play = PropPlay(random.Random(4))
    ball = play.emit("ball", 50, 88, 0)
    coin = play.emit("coin", 70, 88, 0)
    for item in play.items:
        item.grounded = True
        item.vx = item.vy = 0
    assert not play.kick(coin, 1, 1)
    assert not play.collect(ball, 1)
    play.drag(ball, 40, 80, 1)
    assert not play.kick(ball, 1, 1)
    assert {item.id for item in play.candidates(1)} == {coin}
    assert play.collect(coin, 1)
    assert not play.collect(coin, 1)
    play.release(ball, 1)
    item = play.items[0]
    item.grounded = True
    assert not play.kick(ball, 1, 0)
    assert play.kick(ball, 1, -1)
    assert item.vx < 0 and item.vy < 0 and not item.grounded
    item.grounded = True
    item.vx = item.vy = 0
    assert not play.kick(ball, item.born + item.ttl, 1)
    assert not play.candidates(item.born + item.ttl)


def test_gems_are_collectible_and_drag_is_bounded_and_rechecked():
    play = PropPlay(random.Random(5))
    gem = play.emit("gem", 50, 50, 0)
    play.tick(0, 0, 100, 0, 100)
    assert play.drag(gem, -300, 500, 1)
    item = play.items[0]
    assert (item.x, item.y) == (item.radius, 100 - item.radius)
    assert not play.candidates(1) and not play.collect(gem, 1)
    assert play.release(gem, 1.1, 1000, -1000)
    assert (item.vx, item.vy) == (500, -500)
    item.grounded = True
    item.vx = item.vy = 0
    assert play.collect(gem, 2)
    assert not play.drag(gem, 20, 20, 2)


def test_short_lived_effects_and_return_toys():
    play = PropPlay(random.Random(6))
    star = play.emit("star", 50, 50, 0)
    assert 4 <= play.items[0].ttl <= 7
    ball = play.emit("ball", 30, 30, 0)
    assert play.discard(star, 1) is None
    assert play.discard(ball, 1) == "ball"
    assert play.discard(ball, 1) is None
    play.tick(8, 0, 100, 0, 100)
    assert not play.items

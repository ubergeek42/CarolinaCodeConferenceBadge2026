"""nearby -- one LED per badge in earshot. Reasoning in mods/README.md."""

NAME = "nearby"
VERSION = 1
WANTS_PIXELS = True

LED_HZ = 10
COLORS = ((0, 40, 90), (0, 110, 70), (110, 110, 0), (150, 60, 0), (160, 0, 60))


def setup(ctx):
    ctx.state["lit"] = 0.0
    ctx.state["n"] = -1
    ctx.needs_radio = True
    ctx.log("counting badges in earshot")


def tick(ctx, now):
    st = ctx.state
    if ctx.pixels is None or now - st["lit"] < 1.0 / LED_HZ:
        return
    st["lit"] = now
    n = len(ctx.peers.nearby(now)) if ctx.peers is not None else 0
    if n == st["n"]:
        return                          # nothing changed; skip the write
    st["n"] = n
    for i in range(5):
        ctx.pixels[i] = COLORS[i] if i < n else (0, 0, 0)
    ctx.show()


def teardown(ctx):
    if ctx.pixels is not None:
        ctx.pixels.fill((0, 0, 0))
        ctx.show()

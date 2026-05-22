import marimo

__generated_with = "0.23.4"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import asyncio
    import random

    return asyncio, mo, random


@app.cell
def _(mo):
    mo.md("""
    # Async Button Demo

    Hit the button below and watch a multi-stage loader cook.
    """)
    return


@app.cell
def _(mo):
    go = mo.ui.run_button(label="Generate something cool", kind="success")
    go
    return (go,)


@app.cell
async def _(asyncio, go, mo, random):
    mo.stop(not go.value, mo.md("_Waiting for you to press the button..._"))

    stages = [
        ("Warming up the synths", 0.8),
        ("Sampling the latent space", 1.0),
        ("Quantizing to the nearest groove", 0.7),
        ("Mastering the final mix", 0.9),
    ]

    with mo.status.spinner(title="Starting...", subtitle="hold tight") as spinner:
        for title, delay in stages:
            spinner.update(title=title, subtitle=f"~{delay:.1f}s")
            await asyncio.sleep(delay)

    seed = random.randint(1000, 9999)
    bpm = random.randint(80, 160)
    key = random.choice(["C", "D", "E", "F", "G", "A", "B"])
    mode = random.choice(["maj", "min", "dorian", "lydian"])

    mo.md(
        f"""
        ## Done!

        | seed | bpm | key |
        |------|-----|-----|
        | `{seed}` | **{bpm}** | {key} {mode} |

        _press the button again for another roll_
        """
    )
    return


if __name__ == "__main__":
    app.run()

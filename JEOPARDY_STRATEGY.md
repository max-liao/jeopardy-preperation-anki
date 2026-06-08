# Jeopardy! Strategy Guide

---

## Daily Double Strategy

### Placement Rules

Writers deliberately pick Daily Double clues — not random. Key rules:

- **Two-step clues**: require connecting multiple facts, not single recall
- **Bottom rows only**: almost never row 1; 92% land in rows 3–5
- **DJ separation**: both Daily Doubles are always in different categories, spread apart

### Where to Find Them

```
Row 1 ($200/$400)    →  Extremely rare (<1%)
Row 2 ($400/$800)    →  Rare (~7%)
Row 3 ($600/$1200)   →  Common (~27%)
Row 4 ($800/$1600)   →  Prime hotspot (~38%)
Row 5 ($1000/$2000)  →  Common (~27%)
```

- **Row 4, column 1** is the single densest cell historically
- **The Forrest Bounce**: skip rows 1–2, bounce horizontally across row 4 between categories

### Betting by Position

| Situation                  | Bet                            | Why                                  |
| -------------------------- | ------------------------------ | ------------------------------------ |
| Early game, score < $1,000 | $1,000 (max)                   | Low risk, big upside                 |
| Leading — need runaway     | Exact amount to cross 2× 2nd   | Lock before Final                    |
| Leading comfortably        | Enough to stay ahead on a miss | Protect position                     |
| Trailing badly             | All-in                         | Better than coasting to certain loss |
| Hate the category          | $5                             | Burns the clue, protects bankroll    |

**Runaway threshold**: your score > 2× second place. Calculate the exact bet to cross it and stop there — overbetting risks dropping below on a miss.

---

## Holzhauer vs. Jennings

|            | Ken Jennings                | James Holzhauer                          |
| ---------- | --------------------------- | ---------------------------------------- |
| Clue order | Top-down (easy first)       | Bottom-up (hard first)                   |
| DD hunting | Passive                     | Aggressive Forrest Bounce                |
| Wagering   | Conservative                | All-in                                   |
| Edge       | Buzzer speed + recall depth | Risk tolerance + statistical positioning |

### Holzhauer's System

1. **Build cash first** — open at $1,000/$2,000 to maximize bankroll before hitting a DD
2. **Bounce to find DDs** — jump row 4 horizontally; disorients opponents, finds DDs fast
3. **Bet aggressively** — at 95% DD accuracy, conservative bets waste expected value. EV = 0.95W − 0.05W = **+0.90W per dollar wagered**

### Jennings's Adaptation (GOAT 2020)

Jennings's 74-game streak relied on top-down play and buzzer dominance — it worked because no opponent played differently. Against Holzhauer, that strategy was a guaranteed loss.

His fix: abandon top-down, force himself to row 4, match Holzhauer's wager sizes. Combined with superior recall, it beat Holzhauer at his own game.

### When to Use Each

**Holzhauer**: when you need to build a lead, you're in a competitive game, or you're trailing.

**Jennings**: when you already have a runaway — don't introduce variance you don't need.

**Hybrid rule**: play Holzhauer until runaway, then play Jennings to protect it.

---

## Final Jeopardy! Wagering Math

```
Scenario 1: Runaway     →  your score > 2× second place
Scenario 2: First, no runaway  →  you lead, but second can catch you
Scenario 3: Second place  →  chasing a vulnerable leader
```

**Scenario 1 — The Lock**

You win even betting $0. Max safe bet = `score − (2 × 2nd) − 1`.

Example: You $20,000 / 2nd $9,000 → max bet = $20,000 − $18,000 − 1 = **$1,999**

**Scenario 2 — The Shutout Bet**

Second place will bet everything. You must beat their doubled score.

```
shutout_bet = (2 × second_place) − your_score + 1
```

Example: You $16,000 / 2nd $10,000 → bet = $20,000 − $16,000 + 1 = **$4,001**

Hit: you reach $20,001, they reach $20,000. You win by $1.

**Scenario 3 — The Shore Up**

Leader bets their shutout amount. Calculate where they land on a miss, then stay just above it.

Example: Leader $16,000 bets $4,001 → drops to $11,999 on a miss. You have $10,000.

- Can't beat $11,999 on a miss regardless — bet $5 to survive.
- If you're right and they're wrong: any positive wager wins.

**Quick reference**

| Position                | Condition                         | Bet                         |
| ----------------------- | --------------------------------- | --------------------------- |
| 1st (runaway)           | score > 2× 2nd                    | `score − (2 × 2nd) − 1` max |
| 1st (no runaway)        | score ≤ 2× 2nd                    | `(2 × 2nd) − score + 1`     |
| 2nd (leader runaway)    | can't win on miss                 | All-in                      |
| 2nd (leader vulnerable) | `score − (leader_after_miss) − 1` | Shore up                    |
| 3rd                     | can't reach 2nd                   | All-in                      |

---

## Buzzer Timing

### How It Works

Buzzers are **locked out** while the host reads the clue. An off-stage staffer activates them the instant the final syllable lands, triggering ring lights on the board edge. Buzz before activation = **0.25-second lockout penalty**. In a field of elite players, that's a death sentence.

### The Champion Technique

**Don't react to the light — predict it.**

- Reactive: see light → process → press. Adds ~150–200ms lag.
- Anticipatory: internalize host's cadence → press as the last word lands. Fires within 10–30ms of activation.

Both Jennings and Holzhauer describe the same method: track speech rhythm, not the light. Holzhauer used a "pendulum" thumb motion timed to the expected release.

**Training**: watch episodes and press an imaginary buzzer on the host's last syllable. After 50+ clues it becomes automatic.

### Common Mistakes

| Mistake                                | Fix                                                         |
| -------------------------------------- | ----------------------------------------------------------- |
| Buzzing early                          | Wait for the final syllable                                 |
| Rapid-fire clicking                    | One deliberate press only                                   |
| Waiting to confirm you know the answer | Buzz while processing — answer comes after winning the race |

### Buzzer vs. Knowledge Trade-off

On $200 clues, everyone knows the answer — it's a pure buzzer race. On $2,000 clues, only 1–2 players know it — knowledge matters more. Holzhauer's bottom-up strategy minimizes buzzer competition by concentrating play on harder clues where his knowledge edge is decisive.

If your buzz is average but your knowledge is elite: play harder clues first.

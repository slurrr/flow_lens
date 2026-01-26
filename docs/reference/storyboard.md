# Story Board Stress Tests

## Four core Regimes

| Regime       | Dot Size | Halo          | Y    | Meaning             |
| ------------ | -------- | ------------- | ---- | ------------------- |
| Trap         | Large    | Growing       | Low  | Aggression failing  |
| Continuation | Growing  | Growing later | High | Accepted trend      |
| Squeeze      | Large    | Growing       | High | Reflexive perp push |
| Air pocket   | Small    | Small         | High | No force, thin book |

## Scenario: Perp-Led Trap

### Ground truth story

Perps slam shorts (or longs), size is big, more traders pile in, but price does not accept. The move fails once late participants are fully committed.

Your lens must reveal:

- Early concentrated force
- Growing crowding
- Declining effectiveness before reversal.

| Step | X (Control)  | Y (Effectiveness) | Dot Size (Force) | Halo (Dispersion) | One-Glance Read                   |
| ---- | ------------ | ----------------- | ---------------- | ----------------- | --------------------------------- |
| 1    | slight left  | neutral           | small            | none              | nothing happening                 |
| 2    | left         | slight down       | medium           | none              | perps probing                     |
| 3    | left         | down              | medium           | small             | perps pushing, not working        |
| 4    | left more    | down              | large            | small             | aggressive perp push failing      |
| 5    | strong left  | down              | large            | small             | concentrated force, rejected      |
| 6    | strong left  | down              | large            | medium            | others starting to join           |
| 7    | strong left  | slight down       | large            | medium            | crowd building, still not working |
| 8    | strong left  | flat              | large            | medium            | pressure but no progress          |
| 9    | strong left  | slight up (fake)  | large            | medium            | temporary squeeze response        |
| 10   | strong left  | flat              | large            | medium            | move stalls again                 |
| 11   | strong left  | down              | large            | large             | now crowded + ineffective         |
| 12   | strong left  | down more         | large            | large             | heavy effort, still rejected      |
| 13   | left         | down              | medium           | large             | force weakening, crowd still in   |
| 14   | left         | flat              | medium           | large             | trapped participation             |
| 15   | center drift | slight up         | medium           | medium            | control slipping                  |
| 16   | slight right | up                | medium           | medium            | reversal starting                 |
| 17   | right        | up                | medium           | small             | crowd exiting, control flipping   |
| 18   | right        | up                | medium           | small             | new side working                  |
| 19   | right        | up                | small            | small             | cleanup phase                     |
| 20   | right        | up                | small            | none              | trap fully unwound                |

## Scenario: Spot-Led Continuation

### Ground truth story

Spot buying leads, price accepts higher, participation gradually broadens, move becomes crowded but does not stall until late.

### Your lens must show

- Early effective move with low dispersion
- Growing force that continues to work
- Dispersion rising after effectiveness established
- Late crowding but without immediate failure

| Step | X (Control)  | Y (Effectiveness) | Dot Size (Force) | Halo (Dispersion) | One-Glance Read                    |
| ---- | ------------ | ----------------- | ---------------- | ----------------- | ---------------------------------- |
| 1    | slight right | neutral           | small            | none              | nothing yet                        |
| 2    | right        | slight up         | small            | none              | spot probing                       |
| 3    | right        | up                | small            | none              | early acceptance                   |
| 4    | right more   | up                | medium           | small             | spot working, still quiet          |
| 5    | strong right | up                | medium           | small             | clean continuation                 |
| 6    | strong right | up                | medium           | small             | no crowd yet                       |
| 7    | strong right | up                | large            | small             | force building, still concentrated |
| 8    | strong right | up                | large            | medium            | participation starting             |
| 9    | strong right | up                | large            | medium            | broadening but still working       |
| 10   | strong right | up                | large            | medium            | stable trend phase                 |
| 11   | strong right | slight up         | large            | large             | now crowded but effective          |
| 12   | strong right | flat/up           | large            | large             | late but still accepted            |
| 13   | strong right | flat              | medium           | large             | force cooling slightly             |
| 14   | strong right | flat              | medium           | large             | pause, not rejection               |
| 15   | strong right | slight up         | medium           | large             | continuation resumes               |
| 16   | strong right | flat              | medium           | large             | distribution starting              |
| 17   | slight right | flat              | medium           | medium            | participation fading               |
| 18   | slight right | slight down       | small            | medium            | move aging                         |
| 19   | center drift | neutral           | small            | small             | control fading                     |
| 20   | center       | neutral           | small            | none              | trend complete                     |

## Scenario: Perp-Led Squeeze (Working Move)

### Ground truth story

Perps aggressively force positioning, price accepts the move, late traders pile in, dispersion rises rapidly, and the move becomes reflexive before eventual exhaustion.

Key difference from trap:
Effectiveness (Y) stays high.

| Step | X (Control)  | Y (Effectiveness) | Dot Size (Force) | Halo (Dispersion) | One-Glance Read              |
| ---- | ------------ | ----------------- | ---------------- | ----------------- | ---------------------------- |
| 1    | slight left  | neutral           | small            | none              | nothing yet                  |
| 2    | left         | slight up         | medium           | none              | perps probing, working       |
| 3    | left         | up                | medium           | none              | early squeeze signal         |
| 4    | left more    | up                | large            | small             | strong perp force accepted   |
| 5    | strong left  | up                | large            | small             | concentrated squeeze         |
| 6    | strong left  | up                | large            | medium            | others forced in             |
| 7    | strong left  | up                | large            | medium            | reflexivity starting         |
| 8    | strong left  | up                | large            | medium            | working pressure             |
| 9    | strong left  | up                | large            | large             | crowding but still effective |
| 10   | strong left  | up                | large            | large             | mature squeeze               |
| 11   | strong left  | slight up         | large            | large             | late participation           |
| 12   | strong left  | flat/up           | large            | large             | still accepted               |
| 13   | strong left  | flat              | medium           | large             | force cooling                |
| 14   | strong left  | flat              | medium           | large             | crowd heavy                  |
| 15   | left         | slight down       | medium           | large             | exhaustion beginning         |
| 16   | left         | down              | medium           | medium            | unwind starting              |
| 17   | center drift | down              | small            | medium            | squeeze releasing            |
| 18   | right        | down              | small            | small             | control flipping             |
| 19   | right        | neutral           | small            | small             | cleanup                      |
| 20   | right        | neutral           | small            | none              | post-event equilibrium       |

## Scenario: Air Pocket (Movement Without Force)

### Ground truth story 

Liquidity vanishes, price jumps through space. Very little real participation. No broad positioning transfer.

### Your lens must show 

- High effectiveness (Y)
- Low force (small dot)
- Low dispersion (small halo)

If dot gets big here, your force model is broken.

| Step | X (Control)  | Y (Effectiveness) | Dot Size (Force) | Halo (Dispersion) | One-Glance Read        |
| ---- | ------------ | ----------------- | ---------------- | ----------------- | ---------------------- |
| 1    | center       | neutral           | small            | none              | nothing                |
| 2    | slight right | up                | small            | none              | small move             |
| 3    | right        | up                | small            | none              | price lifts easily     |
| 4    | right more   | high up           | small            | none              | air pocket starts      |
| 5    | strong right | high up           | small            | small             | thin book continuation |
| 6    | strong right | high up           | small            | small             | still no force         |
| 7    | strong right | up                | small            | small             | drifting move          |
| 8    | strong right | flat/up           | small            | small             | momentum fading        |
| 9    | strong right | flat              | small            | small             | no follow-through      |
| 10   | slight right | flat              | small            | small             | stall                  |
| 11   | center drift | slight down       | small            | small             | gravity returns        |
| 12   | center       | down              | small            | small             | reversal               |
| 13   | left         | down              | small            | small             | snap back              |
| 14   | left         | flat              | small            | small             | equilibrium returning  |
| 15   | center       | neutral           | small            | none              | move erased            |
| 16   | center       | neutral           | small            | none              | stable                 |
| 17   | center       | neutral           | small            | none              | stable                 |
| 18   | center       | neutral           | small            | none              | stable                 |
| 19   | center       | neutral           | small            | none              | stable                 |
| 20   | center       | neutral           | small            | none              | stable                 |

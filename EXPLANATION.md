# Project Idea and Working Flow

## The idea
Most apps send every question to the most expensive AI model. That wastes money. Sending everything to the cheapest model gives wrong answers.

This project is a smart gateway in the middle. For every question it picks the cheapest model that can still answer correctly, reuses saved answers when safe, and shows how much money was saved without losing quality.

Think: auto-rickshaw for short trips, luxury car for long trips, and a controller that chooses correctly every time.

## How it works (flow)
1. **User sends a question** to the gateway with `model="auto"`.
2. **Check saved answers** — exact same question seen before? Similar question with same meaning? Same document, new question? If yes and all safety checks pass, return the saved answer with zero AI cost.
3. **Understand the question** — what type (general, math, code, reasoning), how hard (0–1), how confident.
4. **Pick a model** — cheapest model whose measured skill passes the question's need. Easy → cheap, hard → strong, unsure → safest.
5. **Generate the answer** with that model.
6. **Check quality** — score the answer. If it fails, try the next-better model and add both costs honestly.
7. **Save + measure** — save only correct answers for next time; report real cost vs always-expensive baseline and net savings.

## Example
- "What is an API?" → easy → cheap model → correct → saved, ~60% cheaper.
- "Design a fault-tolerant banking ledger" → hard → strong model directly (no cheap gamble).
- Ask "What is 15% of 200?" then "Calculate 15 percent of 200" → second one is a safe cache hit, no AI call.
- Ask `2+2` then `2*2` → blocked on purpose (different operator), safety over savings.

# SEC Form 4 transaction codes

Form 4 (Statement of Changes in Beneficial Ownership) is filed within
two business days of a transaction by any Section 16 insider: officers,
directors, and 10%+ shareholders. Each transaction carries a
one-letter `transaction_code` from SEC Rule 16a-3. The code plus the
`aff_10b5_one` flag classifies the signal.

## Signal categories used by this skill

### Conviction buy (`conviction_buy`)

- **P**: Open-market purchase of a non-derivative security.

This is the clean bullish signal. Insiders rarely open-market buy
unless they see something the market doesn't. Grants and derivative
exercises are compensation; a P purchase means the insider chose to
spend their own money at the current market price.

### Discretionary sale (`discretionary_sale`)

- **S** with `aff_10b5_one=false`: Open-market sale, not under a
  pre-committed plan.

Noisier than a P purchase. Insiders sell for reasons unrelated to
their view of the business: diversification (paper wealth is
concentrated in employer stock), taxes (RSU vesting, option exercise
recovery), personal liquidity (home purchase, divorce). But clustered
discretionary sales or unusually large ones by a single officer near
a stock high are informative.

### Scheduled sale (`scheduled_sale`)

- **S** with `aff_10b5_one=true`: Sale under a Rule 10b5-1 trading
  plan.

10b5-1 plans are announced months in advance (typical setup: quarterly
sales of N shares regardless of price). The insider committed to the
sale schedule when they had no material non-public information; the
individual sale carries near-zero signal. Filtered from sentiment.

Caveat: a 10b5-1 plan *adopted* right before a material event is
itself a red flag. The endpoint doesn't expose plan adoption date;
that's in filing footnotes.

### Routine comp (`routine_comp`)

- **A**: Grant, award, or other acquisition (e.g., RSU vesting).
- **M**: Exercise or conversion of derivative security. Typically
  paired with an F row for the tax withholding.
- **F**: Payment of exercise price or tax liability by delivering
  or withholding securities. The insider isn't choosing to sell
  discretionarily; they're paying the tax on a vest.

Non-informative. Reported for transparency, not for signal.

### Non-informative (`non_informative`)

Everything else. Reported here for completeness so a downstream
consumer can drill into the JSON if needed.

- **G**: Bona fide gift.
- **D**: Return of securities to the issuer.
- **I**: Discretionary transaction (an SEC-defined type, not
  discretionary in the informal sense).
- **J**: Other transaction (catch-all).
- **V**: Voluntarily reported earlier than required.
- **X**: Exercise of an in-the-money or at-the-money derivative.
- **Z**: Deposit into or withdrawal from a voting trust.
- **L**: Small acquisition under Rule 16a-6.
- **W**: Acquisition or disposition by will or the laws of descent
  and distribution.
- **C**: Conversion of a derivative security.
- **E**: Expiration of a short derivative position.
- **H**: Expiration of a long derivative position.
- **O**: Exercise of an out-of-the-money derivative.
- **K**: Transaction in equity swap.

## Non-derivative vs derivative

The endpoint tags each row with `security_type: "non_derivative"` or
`"derivative"`. Open-market buys and sales that count as signal are
almost always on non-derivative securities (common stock). A code-P
row on a derivative would be a purchase of an option or a warrant,
which has different semantics; the classifier requires
`security_type == "non_derivative"` for the `conviction_buy` and
`discretionary_sale` categories to fire.

## Officer titles

Massive returns `officer_title` as a free-form string from the filing
(examples: "Chief Executive Officer", "EVP, CFO", "President and
COO"). The role label in this skill's output preserves the raw title
so a downstream consumer can filter on it. When the title is empty,
the label is just `Officer`.

## References

- [SEC Form 4 filing instructions](https://www.sec.gov/about/forms/form4data.pdf)
- [Rule 10b5-1 background](https://www.sec.gov/rules/2000/33-7881.htm)
- Bhattacharya, Nikoulina, Sadka (2013), *When No Law Is Better Than a
  Good Law*: on the correlation between insider trading enforcement
  and the informativeness of Form 4 filings.

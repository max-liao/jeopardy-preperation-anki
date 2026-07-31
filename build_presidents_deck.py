"""Build a standalone 'US Presidents' Anki deck: number, years served, VP(s), notable events."""

from __future__ import annotations

from dataclasses import dataclass

import genanki

DECK_ID = 1958372645123
MODEL_ID = 1958372645456
DECK_NAME = "US Presidents"
OUTPUT_PATH = "US Presidents.apkg"

CSS = """
.card {
    --bg: #fafafa;
    --fg: #1a1a1a;
    --accent: #8b0000;
    --muted: #555555;
    --muted2: #333333;
    font-family: Georgia, 'Times New Roman', serif;
    text-align: center;
    padding: 20px;
    background-color: var(--bg);
    color: var(--fg);
}
.night_mode.card, .night_mode .card {
    --bg: #1e1e1e;
    --fg: #e8e8e8;
    --accent: #ff6b6b;
    --muted: #bbbbbb;
    --muted2: #dddddd;
}
@media (prefers-color-scheme: dark) {
    .card {
        --bg: #1e1e1e;
        --fg: #e8e8e8;
        --accent: #ff6b6b;
        --muted: #bbbbbb;
        --muted2: #dddddd;
    }
}
.prompt {
    font-size: 20px;
    color: var(--muted);
}
.number {
    font-size: 32px;
    font-weight: bold;
    color: var(--accent);
    margin-top: 18px;
}
.name {
    font-size: 32px;
    font-weight: bold;
    margin-top: 10px;
}
.party {
    font-size: 20px;
    font-weight: normal;
    color: var(--muted);
}
.years {
    font-size: 22px;
    color: var(--muted2);
    margin-top: 4px;
}
.section-label {
    font-size: 16px;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--accent);
    margin-top: 18px;
}
.vp, .events {
    font-size: 18px;
    text-align: left;
    display: inline-block;
    margin-top: 4px;
}
.events ul {
    margin: 4px 0 0 0;
    padding-left: 22px;
}
"""

FRONT_TEMPLATE = """
<div class="prompt">Which U.S. president is this?</div>
<div class="events">{{NotableEvents}}</div>
"""

BACK_TEMPLATE = """
{{FrontSide}}
<hr id="answer">
<div class="number">#{{Number}}</div>
<div class="name">{{Name}} <span class="party">({{Party}})</span></div>
<div class="years">{{Years}}</div>
<div class="section-label">Vice President(s)</div>
<div class="vp">{{VicePresidents}}</div>
"""


@dataclass(frozen=True)
class Presidency:
    number: str
    name: str
    years: str
    party: str
    vice_presidents: tuple[str, ...]
    notable_events: tuple[str, ...]


PRESIDENCIES: tuple[Presidency, ...] = (
    Presidency(
        "1",
        "George Washington",
        "1789–1797",
        "Unaffiliated",
        ("John Adams",),
        (
            "Whiskey Rebellion suppressed",
            "Bill of Rights ratified (1791)",
            "Set two-term precedent",
            "Proclamation of Neutrality (1793)",
        ),
    ),
    Presidency(
        "2",
        "John Adams",
        "1797–1801",
        "Federalist",
        ("Thomas Jefferson",),
        (
            "XYZ Affair",
            "Alien and Sedition Acts (1798)",
            "Avoided war with France (Quasi-War settled)",
        ),
    ),
    Presidency(
        "3",
        "Thomas Jefferson",
        "1801–1809",
        "Democratic-Republican",
        ("Aaron Burr (1801–1805)", "George Clinton (1805–1809)"),
        (
            "Louisiana Purchase (1803)",
            "Lewis and Clark Expedition",
            "Embargo Act of 1807",
        ),
    ),
    Presidency(
        "4",
        "James Madison",
        "1809–1817",
        "Democratic-Republican",
        (
            "George Clinton (1809–1812, died in office)",
            "Elbridge Gerry (1813–1814, died in office)",
        ),
        (
            "War of 1812",
            "White House burned by the British (1814)",
            "“Star-Spangled Banner” written during his term",
        ),
    ),
    Presidency(
        "5",
        "James Monroe",
        "1817–1825",
        "Democratic-Republican",
        ("Daniel D. Tompkins",),
        (
            "Declared the Americas off-limits to further European colonization (1823)",
            "Missouri Compromise (1820)",
            "“Era of Good Feelings”",
        ),
    ),
    Presidency(
        "6",
        "John Quincy Adams",
        "1825–1829",
        "Democratic-Republican",
        ("John C. Calhoun",),
        (
            "Elected via the “Corrupt Bargain” of 1824",
            "Championed internal improvements (roads, canals)",
        ),
    ),
    Presidency(
        "7",
        "Andrew Jackson",
        "1829–1837",
        "Democratic",
        ("John C. Calhoun (1829–1832, resigned)", "Martin Van Buren (1833–1837)"),
        (
            "Indian Removal Act & Trail of Tears",
            "Nullification Crisis",
            "Killed the Second Bank of the United States (“Bank War”)",
        ),
    ),
    Presidency(
        "8",
        "Martin Van Buren",
        "1837–1841",
        "Democratic",
        ("Richard Mentor Johnson",),
        ("Panic of 1837", "Continued Indian removal policy"),
    ),
    Presidency(
        "9",
        "William Henry Harrison",
        "1841 (31 days)",
        "Whig",
        ("John Tyler",),
        (
            "Died of illness weeks after inauguration",
            "Shortest presidency in U.S. history",
        ),
    ),
    Presidency(
        "10",
        "John Tyler",
        "1841–1845",
        "Whig (expelled) / Independent",
        ("None (office vacant)",),
        (
            "First VP to succeed a president who died in office",
            "Established that a VP fully assumes the presidency (not just its duties) on a president's death",
            "Annexation of Texas",
        ),
    ),
    Presidency(
        "11",
        "James K. Polk",
        "1845–1849",
        "Democratic",
        ("George M. Dallas",),
        (
            "Mexican-American War",
            "Oregon Treaty (1846)",
            "Major territorial expansion (Manifest Destiny)",
        ),
    ),
    Presidency(
        "12",
        "Zachary Taylor",
        "1849–1850",
        "Whig",
        ("Millard Fillmore",),
        ("Died in office (1850)", "Compromise of 1850 debates began under his term"),
    ),
    Presidency(
        "13",
        "Millard Fillmore",
        "1850–1853",
        "Whig",
        ("None",),
        (
            "Signed the Compromise of 1850, incl. Fugitive Slave Act",
            "Sent Commodore Perry's expedition to Japan",
        ),
    ),
    Presidency(
        "14",
        "Franklin Pierce",
        "1853–1857",
        "Democratic",
        ("William R. King (died shortly after taking office)",),
        ("Kansas-Nebraska Act (1854)", "Gadsden Purchase"),
    ),
    Presidency(
        "15",
        "James Buchanan",
        "1857–1861",
        "Democratic",
        ("John C. Breckinridge",),
        (
            "Dred Scott decision",
            "Southern states began seceding",
            "Only president who never married",
        ),
    ),
    Presidency(
        "16",
        "Abraham Lincoln",
        "1861–1865",
        "Republican",
        ("Hannibal Hamlin (1861–1865)", "Andrew Johnson (1865)"),
        (
            "Civil War",
            "Emancipation Proclamation (1863)",
            "Gettysburg Address",
            "Assassinated by John Wilkes Booth",
        ),
    ),
    Presidency(
        "17",
        "Andrew Johnson",
        "1865–1869",
        "National Union (Democrat)",
        ("None",),
        (
            "Reconstruction",
            "Impeached by the House, acquitted by one Senate vote",
            "Purchase of Alaska (1867)",
        ),
    ),
    Presidency(
        "18",
        "Ulysses S. Grant",
        "1869–1877",
        "Republican",
        ("Schuyler Colfax (1869–1873)", "Henry Wilson (1873–1875, died in office)"),
        (
            "Reconstruction enforcement",
            "Panic of 1873",
            "Administration scandals (Whiskey Ring, Crédit Mobilier)",
        ),
    ),
    Presidency(
        "19",
        "Rutherford B. Hayes",
        "1877–1881",
        "Republican",
        ("William A. Wheeler",),
        (
            "Disputed 1876 election settled by the Compromise of 1877",
            "End of Reconstruction",
        ),
    ),
    Presidency(
        "20",
        "James A. Garfield",
        "1881 (200 days)",
        "Republican",
        ("Chester A. Arthur",),
        ("Assassinated by Charles Guiteau", "Death spurred civil service reform"),
    ),
    Presidency(
        "21",
        "Chester A. Arthur",
        "1881–1885",
        "Republican",
        ("None",),
        ("Pendleton Civil Service Reform Act (1883)",),
    ),
    Presidency(
        "22 & 24",
        "Grover Cleveland",
        "1885–1889, 1893–1897",
        "Democratic",
        (
            "Thomas A. Hendricks (1885, died in office)",
            "Adlai Stevenson I (1893–1897)",
        ),
        (
            "Only president to serve two nonconsecutive terms until Trump (2025)",
            "Vetoed a record number of bills",
            "Panic of 1893",
            "Pullman Strike (1894)",
        ),
    ),
    Presidency(
        "23",
        "Benjamin Harrison",
        "1889–1893",
        "Republican",
        ("Levi P. Morton",),
        (
            "Sherman Antitrust Act (1890)",
            "Sherman Silver Purchase Act (1890)",
            "Admitted six new states",
        ),
    ),
    Presidency(
        "25",
        "William McKinley",
        "1897–1901",
        "Republican",
        ("Garret Hobart (1897–1899, died in office)", "Theodore Roosevelt (1901)"),
        (
            "Spanish-American War (1898)",
            "Annexation of Hawaii and the Philippines",
            "Assassinated by Leon Czolgosz",
        ),
    ),
    Presidency(
        "26",
        "Theodore Roosevelt",
        "1901–1909",
        "Republican",
        ("None (1901–1905)", "Charles W. Fairbanks (1905–1909)"),
        (
            "Trust-busting",
            "Began construction of the Panama Canal",
            "“Square Deal” reforms",
            "Nobel Peace Prize (1906)",
        ),
    ),
    Presidency(
        "27",
        "William Howard Taft",
        "1909–1913",
        "Republican",
        ("James S. Sherman",),
        (
            "“Dollar diplomacy”",
            "Filed more antitrust suits than Theodore Roosevelt",
            "Later became Chief Justice of the U.S.",
        ),
    ),
    Presidency(
        "28",
        "Woodrow Wilson",
        "1913–1921",
        "Democratic",
        ("Thomas R. Marshall",),
        (
            "World War I",
            "Federal Reserve Act (1913)",
            "19th Amendment (women's suffrage)",
            "Proposed the League of Nations",
        ),
    ),
    Presidency(
        "29",
        "Warren G. Harding",
        "1921–1923",
        "Republican",
        ("Calvin Coolidge",),
        ("Teapot Dome scandal", "Died in office (1923)"),
    ),
    Presidency(
        "30",
        "Calvin Coolidge",
        "1923–1929",
        "Republican",
        ("None (1923–1925)", "Charles G. Dawes (1925–1929)"),
        (
            "“Roaring Twenties” economic prosperity",
            "Small-government, pro-business conservatism",
        ),
    ),
    Presidency(
        "31",
        "Herbert Hoover",
        "1929–1933",
        "Republican",
        ("Charles Curtis",),
        (
            "Stock market crash of 1929",
            "Start of the Great Depression",
            "Smoot-Hawley Tariff Act (1930)",
        ),
    ),
    Presidency(
        "32",
        "Franklin D. Roosevelt",
        "1933–1945",
        "Democratic",
        (
            "John Nance Garner (1933–1941)",
            "Henry A. Wallace (1941–1945)",
            "Harry S. Truman (1945)",
        ),
        (
            "The New Deal",
            "World War II",
            "Only president elected to four terms",
            "Died in office (1945)",
        ),
    ),
    Presidency(
        "33",
        "Harry S. Truman",
        "1945–1953",
        "Democratic",
        ("None (1945–1949)", "Alben W. Barkley (1949–1953)"),
        (
            "Ended WWII / atomic bombings of Japan",
            "Marshall Plan",
            "Korean War",
            "Pledged U.S. support for countries resisting communism (containment policy)",
        ),
    ),
    Presidency(
        "34",
        "Dwight D. Eisenhower",
        "1953–1961",
        "Republican",
        ("Richard Nixon",),
        (
            "Interstate Highway System",
            "Cold War tensions / Sputnik response",
            "Sent troops to desegregate Little Rock Central High (1957)",
        ),
    ),
    Presidency(
        "35",
        "John F. Kennedy",
        "1961–1963",
        "Democratic",
        ("Lyndon B. Johnson",),
        (
            "Cuban Missile Crisis (1962)",
            "Bay of Pigs Invasion (1961)",
            "Assassinated in Dallas (1963)",
        ),
    ),
    Presidency(
        "36",
        "Lyndon B. Johnson",
        "1963–1969",
        "Democratic",
        ("None (1963–1965)", "Hubert Humphrey (1965–1969)"),
        (
            "Civil Rights Act of 1964",
            "Voting Rights Act of 1965",
            "“Great Society” programs",
            "Escalation of the Vietnam War",
        ),
    ),
    Presidency(
        "37",
        "Richard Nixon",
        "1969–1974",
        "Republican",
        ("Spiro Agnew (1969–1973, resigned)", "Gerald Ford (1973–1974)"),
        (
            "Watergate scandal and resignation",
            "Opened relations with China (1972)",
            "Ended the Vietnam War draft",
        ),
    ),
    Presidency(
        "38",
        "Gerald Ford",
        "1974–1977",
        "Republican",
        ("Nelson Rockefeller",),
        (
            "Pardoned Richard Nixon",
            "Only president never elected as VP or president",
            "Fall of Saigon (1975)",
        ),
    ),
    Presidency(
        "39",
        "Jimmy Carter",
        "1977–1981",
        "Democratic",
        ("Walter Mondale",),
        ("Camp David Accords (1978)", "Iran Hostage Crisis", "1970s energy crisis"),
    ),
    Presidency(
        "40",
        "Ronald Reagan",
        "1981–1989",
        "Republican",
        ("George H. W. Bush",),
        (
            "Supply-side, tax-cutting economic policy (“trickle-down economics”)",
            "Diplomacy that hastened the end of the Cold War",
            "Iran-Contra affair",
            "Survived an assassination attempt (1981)",
        ),
    ),
    Presidency(
        "41",
        "George H. W. Bush",
        "1989–1993",
        "Republican",
        ("Dan Quayle",),
        (
            "Gulf War (1991)",
            "Fall of the Berlin Wall / Soviet collapse",
            "Americans with Disabilities Act (1990)",
        ),
    ),
    Presidency(
        "42",
        "Bill Clinton",
        "1993–2001",
        "Democratic",
        ("Al Gore",),
        (
            "Economic boom and budget surplus",
            "NAFTA (1994)",
            "Impeached over the Lewinsky scandal (acquitted)",
        ),
    ),
    Presidency(
        "43",
        "George W. Bush",
        "2001–2009",
        "Republican",
        ("Dick Cheney",),
        (
            "September 11 attacks (2001)",
            "Wars in Afghanistan and Iraq",
            "2008 financial crisis",
        ),
    ),
    Presidency(
        "44",
        "Barack Obama",
        "2009–2017",
        "Democratic",
        ("Joe Biden",),
        (
            "Affordable Care Act (2010)",
            "Killing of Osama bin Laden (2011)",
            "First Black president",
        ),
    ),
    Presidency(
        "45 & 47",
        "Donald Trump",
        "2017–2021, 2025–present",
        "Republican",
        ("Mike Pence (2017–2021)", "JD Vance (2025–present)"),
        (
            "Second president to serve two nonconsecutive terms (after Grover Cleveland)",
            "Tax Cuts and Jobs Act (2017)",
            "COVID-19 pandemic response (2020)",
            "Impeached twice during first term (acquitted both times)",
            "Major tariff policy overhaul (second term)",
        ),
    ),
    Presidency(
        "46",
        "Joe Biden",
        "2021–2025",
        "Democratic",
        ("Kamala Harris",),
        (
            "Infrastructure Investment and Jobs Act (2021)",
            "U.S. withdrawal from Afghanistan (2021)",
            "Oldest person to serve as president",
        ),
    ),
)


def build_model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        "US Presidents",
        fields=[
            {"name": "Number"},
            {"name": "Name"},
            {"name": "Years"},
            {"name": "Party"},
            {"name": "VicePresidents"},
            {"name": "NotableEvents"},
        ],
        templates=[
            {
                "name": "President Card",
                "qfmt": FRONT_TEMPLATE,
                "afmt": BACK_TEMPLATE,
            }
        ],
        css=CSS,
    )


def format_list(items: tuple[str, ...]) -> str:
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def build_note(model: genanki.Model, presidency: Presidency) -> genanki.Note:
    return genanki.Note(
        model=model,
        fields=[
            presidency.number,
            presidency.name,
            presidency.years,
            presidency.party,
            format_list(presidency.vice_presidents),
            format_list(presidency.notable_events),
        ],
        guid=genanki.guid_for("us-presidents", presidency.number),
    )


def main() -> None:
    model = build_model()
    deck = genanki.Deck(DECK_ID, DECK_NAME)
    for presidency in PRESIDENCIES:
        deck.add_note(build_note(model, presidency))
    genanki.Package(deck).write_to_file(OUTPUT_PATH)
    print(f"Wrote {len(PRESIDENCIES)} cards to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

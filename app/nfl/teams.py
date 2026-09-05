"""NFL team nickname and abbreviation resolution for official HTML sources."""

from __future__ import annotations

from app.nfl.identity import normalize_team


TEAM_NICKNAMES = {
    "CARDINALS": "ARI",
    "ARIZONA": "ARI",
    "ARIZONA CARDINALS": "ARI",
    "FALCONS": "ATL",
    "ATLANTA": "ATL",
    "ATLANTA FALCONS": "ATL",
    "RAVENS": "BAL",
    "BALTIMORE": "BAL",
    "BALTIMORE RAVENS": "BAL",
    "BILLS": "BUF",
    "BUFFALO": "BUF",
    "BUFFALO BILLS": "BUF",
    "PANTHERS": "CAR",
    "CAROLINA": "CAR",
    "CAROLINA PANTHERS": "CAR",
    "BEARS": "CHI",
    "CHICAGO": "CHI",
    "CHICAGO BEARS": "CHI",
    "BENGALS": "CIN",
    "CINCINNATI": "CIN",
    "CINCINNATI BENGALS": "CIN",
    "BROWNS": "CLE",
    "CLEVELAND": "CLE",
    "CLEVELAND BROWNS": "CLE",
    "COWBOYS": "DAL",
    "DALLAS": "DAL",
    "DALLAS COWBOYS": "DAL",
    "BRONCOS": "DEN",
    "DENVER": "DEN",
    "DENVER BRONCOS": "DEN",
    "LIONS": "DET",
    "DETROIT": "DET",
    "DETROIT LIONS": "DET",
    "PACKERS": "GB",
    "GREEN BAY": "GB",
    "GREEN BAY PACKERS": "GB",
    "TEXANS": "HOU",
    "HOUSTON": "HOU",
    "HOUSTON TEXANS": "HOU",
    "COLTS": "IND",
    "INDIANAPOLIS": "IND",
    "INDIANAPOLIS COLTS": "IND",
    "JAGUARS": "JAX",
    "JAGS": "JAX",
    "JACKSONVILLE": "JAX",
    "JACKSONVILLE JAGUARS": "JAX",
    "CHIEFS": "KC",
    "KANSAS CITY": "KC",
    "KANSAS CITY CHIEFS": "KC",
    "RAIDERS": "LV",
    "LAS VEGAS": "LV",
    "LAS VEGAS RAIDERS": "LV",
    "CHARGERS": "LAC",
    "LOS ANGELES CHARGERS": "LAC",
    "RAMS": "LAR",
    "LOS ANGELES RAMS": "LAR",
    "DOLPHINS": "MIA",
    "MIAMI": "MIA",
    "MIAMI DOLPHINS": "MIA",
    "VIKINGS": "MIN",
    "MINNESOTA": "MIN",
    "MINNESOTA VIKINGS": "MIN",
    "PATRIOTS": "NE",
    "NEW ENGLAND": "NE",
    "NEW ENGLAND PATRIOTS": "NE",
    "SAINTS": "NO",
    "NEW ORLEANS": "NO",
    "NEW ORLEANS SAINTS": "NO",
    "GIANTS": "NYG",
    "NEW YORK GIANTS": "NYG",
    "JETS": "NYJ",
    "NEW YORK JETS": "NYJ",
    "EAGLES": "PHI",
    "PHILADELPHIA": "PHI",
    "PHILADELPHIA EAGLES": "PHI",
    "STEELERS": "PIT",
    "PITTSBURGH": "PIT",
    "PITTSBURGH STEELERS": "PIT",
    "49ERS": "SF",
    "NINERS": "SF",
    "FORTY NINERS": "SF",
    "SAN FRANCISCO": "SF",
    "SAN FRANCISCO 49ERS": "SF",
    "SEAHAWKS": "SEA",
    "SEATTLE": "SEA",
    "SEATTLE SEAHAWKS": "SEA",
    "BUCCANEERS": "TB",
    "BUCS": "TB",
    "TAMPA BAY": "TB",
    "TAMPA BAY BUCCANEERS": "TB",
    "TITANS": "TEN",
    "TENNESSEE": "TEN",
    "TENNESSEE TITANS": "TEN",
    "COMMANDERS": "WAS",
    "WASHINGTON": "WAS",
    "WASHINGTON COMMANDERS": "WAS",
}
KNOWN_ABBREVIATIONS = frozenset(TEAM_NICKNAMES.values())


def resolve_team_label(text: str) -> str | None:
    """Resolve a nickname, city, or abbreviation to a canonical NFL team code."""

    heading = " ".join(text.split()).upper()
    if not heading:
        return None
    if heading in TEAM_NICKNAMES:
        return TEAM_NICKNAMES[heading]
    normalized = normalize_team(heading)
    if normalized in KNOWN_ABBREVIATIONS:
        return normalized
    return None

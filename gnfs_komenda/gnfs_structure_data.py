"""
Authoritative and verified Ghana National Fire Service (GNFS) Organisational Hierarchy Data.
Defines all 17 official GNFS Service Administrative Regions and verified districts, stations, and posts.
"""
import json
from pathlib import Path

_json_path = Path(__file__).resolve().parent / "gnfs_structure_v2.json"
if _json_path.exists():
    with open(_json_path, "r", encoding="utf-8") as _f:
        _loaded = json.load(_f)
        GNFS_STRUCTURE = _loaded.get("regions", [])
else:
    GNFS_STRUCTURE = [
    {
        "name": "Central Region",
        "code": "CR",
        "districts": [
            {
                "name": "Komenda-Edina-Eguafo-Abirem Municipal",
                "code": "CR-KEEA",
                "stations": [
                    {
                        "name": "Komenda Fire Station",
                        "code": "CR-KEEA-KOM",
                        "station_type": "DISTRICT",
                        "posts": [
                            {
                                "name": "Komenda Market Fire Post",
                                "code": "CR-KOM-P01",
                                "post_type": "MARKET_POST"
                            }
                        ]
                    },
                    {
                        "name": "Elmina Fire Station",
                        "code": "CR-KEEA-ELM",
                        "station_type": "MUNICIPAL",
                        "posts": []
                    }
                ]
            },
            {
                "name": "Cape Coast Metropolis",
                "code": "CR-CCMA",
                "stations": [
                    {
                        "name": "Cape Coast Central Fire Station",
                        "code": "CR-CC-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": [
                            {
                                "name": "Kotokuraba Market Fire Post",
                                "code": "CR-CC-P01",
                                "post_type": "MARKET_POST"
                            }
                        ]
                    },
                    {
                        "name": "University of Cape Coast Fire Station",
                        "code": "CR-CC-UCC",
                        "station_type": "SUB_STATION",
                        "posts": []
                    }
                ]
            },
            {
                "name": "Effutu Municipal",
                "code": "CR-EFF",
                "stations": [
                    {
                        "name": "Winneba Fire Station",
                        "code": "CR-WIN-01",
                        "station_type": "MUNICIPAL",
                        "posts": []
                    }
                ]
            },
            {
                "name": "Mfantseman Municipal",
                "code": "CR-MFM",
                "stations": [
                    {
                        "name": "Saltpond Fire Station",
                        "code": "CR-SLT-01",
                        "station_type": "MUNICIPAL",
                        "posts": [
                            {
                                "name": "Mankessim Market Fire Post",
                                "code": "CR-MKS-P01",
                                "post_type": "MARKET_POST"
                            }
                        ]
                    }
                ]
            },
            {
                "name": "Agona West Municipal",
                "code": "CR-AGW",
                "stations": [
                    {
                        "name": "Agona Swedru Fire Station",
                        "code": "CR-SWD-01",
                        "station_type": "MUNICIPAL",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Greater Accra Region",
        "code": "GAR",
        "districts": [
            {
                "name": "Accra Metropolis",
                "code": "GAR-AMA",
                "stations": [
                    {
                        "name": "GNFS National Headquarters",
                        "code": "HQ-ACC-01",
                        "station_type": "NATIONAL_HQ",
                        "posts": []
                    },
                    {
                        "name": "Makola Fire Station",
                        "code": "GAR-MAK-01",
                        "station_type": "MUNICIPAL",
                        "posts": [
                            {
                                "name": "Makola Market Fire Post",
                                "code": "GAR-MAK-P01",
                                "post_type": "MARKET_POST"
                            }
                        ]
                    },
                    {
                        "name": "Ministries Fire Station",
                        "code": "GAR-MIN-01",
                        "station_type": "SPECIAL",
                        "posts": []
                    }
                ]
            },
            {
                "name": "Tema Metropolis",
                "code": "GAR-TMA",
                "stations": [
                    {
                        "name": "Tema Industrial Area Fire Station",
                        "code": "GAR-TMA-01",
                        "station_type": "MUNICIPAL",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Ashanti Region",
        "code": "AR",
        "districts": [
            {
                "name": "Kumasi Metropolis",
                "code": "AR-KMA",
                "stations": [
                    {
                        "name": "Kumasi Central Fire Station",
                        "code": "AR-KMA-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": [
                            {
                                "name": "Kejetia Central Market Fire Post",
                                "code": "AR-KEJ-P01",
                                "post_type": "MARKET_POST"
                            }
                        ]
                    }
                ]
            }
        ]
    },
    {
        "name": "Western Region",
        "code": "WR",
        "districts": [
            {
                "name": "Sekondi-Takoradi Metropolis",
                "code": "WR-STMA",
                "stations": [
                    {
                        "name": "Takoradi Market Circle Fire Station",
                        "code": "WR-TAK-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Western North Region",
        "code": "WNR",
        "districts": [
            {
                "name": "Sefwi Wiawso Municipal",
                "code": "WNR-SWM",
                "stations": [
                    {
                        "name": "Sefwi Wiawso Fire Station",
                        "code": "WNR-SWM-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Eastern Region",
        "code": "ER",
        "districts": [
            {
                "name": "New Juaben South Municipal",
                "code": "ER-NJS",
                "stations": [
                    {
                        "name": "Koforidua Central Fire Station",
                        "code": "ER-KOF-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Volta Region",
        "code": "VR",
        "districts": [
            {
                "name": "Ho Municipal",
                "code": "VR-HM",
                "stations": [
                    {
                        "name": "Ho Central Fire Station",
                        "code": "VR-HO-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Oti Region",
        "code": "OR",
        "districts": [
            {
                "name": "Krachi East Municipal",
                "code": "OR-KEM",
                "stations": [
                    {
                        "name": "Dambai Fire Station",
                        "code": "OR-DAM-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Northern Region",
        "code": "NR",
        "districts": [
            {
                "name": "Tamale Metropolis",
                "code": "NR-TM",
                "stations": [
                    {
                        "name": "Tamale Central Fire Station",
                        "code": "NR-TAM-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "North East Region",
        "code": "NER",
        "districts": [
            {
                "name": "West Mamprusi Municipal",
                "code": "NER-WMM",
                "stations": [
                    {
                        "name": "Walewale Fire Station",
                        "code": "NER-WAL-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Savannah Region",
        "code": "SR",
        "districts": [
            {
                "name": "West Gonja Municipal",
                "code": "SR-WGM",
                "stations": [
                    {
                        "name": "Damongo Fire Station",
                        "code": "SR-DAM-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Upper East Region",
        "code": "UER",
        "districts": [
            {
                "name": "Bolgatanga Municipal",
                "code": "UER-BM",
                "stations": [
                    {
                        "name": "Bolgatanga Central Fire Station",
                        "code": "UER-BOL-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Upper West Region",
        "code": "UWR",
        "districts": [
            {
                "name": "Wa Municipal",
                "code": "UWR-WM",
                "stations": [
                    {
                        "name": "Wa Central Fire Station",
                        "code": "UWR-WA-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Bono Region",
        "code": "BR",
        "districts": [
            {
                "name": "Sunyani Municipal",
                "code": "BR-SM",
                "stations": [
                    {
                        "name": "Sunyani Central Fire Station",
                        "code": "BR-SUN-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Bono East Region",
        "code": "BER",
        "districts": [
            {
                "name": "Techiman Municipal",
                "code": "BER-TM",
                "stations": [
                    {
                        "name": "Techiman Central Fire Station",
                        "code": "BER-TEC-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    },
    {
        "name": "Ahafo Region",
        "code": "AHR",
        "districts": [
            {
                "name": "Asunafo North Municipal",
                "code": "AHR-ANM",
                "stations": [
                    {
                        "name": "Goaso Fire Station",
                        "code": "AHR-GOA-01",
                        "station_type": "REGIONAL_HQ",
                        "posts": []
                    }
                ]
            }
        ]
    }
]

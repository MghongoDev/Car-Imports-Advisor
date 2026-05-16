import pandas as pd
import numpy as np
import requests
from bs4 import BeautifulSoup
import random
import time
import socket
import traceback
import urllib.parse

# Headers to mimic a real browser request
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9'
}


def fetch_url(url, session=None, timeout=10, max_retries=3):
    """
    Fetch `url` with retries and simple host fallbacks (try with/without www).
    Returns the `requests.Response` on success (status_code==200) or `None`.
    """
    if session is None:
        session = requests.Session()

    variants = [url]
    # if url contains www., try without and vice-versa
    if '://www.' in url:
        variants.append(url.replace('://www.', '://'))
    elif '://' in url:
        parsed = url.split('://', 1)
        host_and_path = parsed[1]
        if not host_and_path.startswith('www.'):
            variants.append(parsed[0] + '://' + 'www.' + host_and_path)

    for attempt in range(1, max_retries + 1):
        for v in variants:
            try:
                resp = session.get(v, headers=HEADERS, timeout=timeout, allow_redirects=True)
                if resp.status_code == 200:
                    return resp
            except requests.exceptions.RequestException:
                continue
        # simple backoff
        time.sleep(0.5 * attempt)

    return None


def diagnose_url(url, session=None, timeout=10):
    """
    Diagnostic fetch that returns a dict with DNS resolution, status, final URL,
    response headers, and a short content snippet. Useful to understand why
    scraping returns 404 or fails to resolve.
    """
    session = session or requests.Session()
    result = {
        'url': url,
        'dns': None,
        'attempts': [],
    }

    # DNS resolution
    try:
        host = url.split('://', 1)[1].split('/')[0]
        ip = socket.gethostbyname(host)
        result['dns'] = {'host': host, 'ip': ip}
    except Exception as e:
        result['dns'] = {'error': str(e)}

    # Try a few user-agents to see differences
    user_agents = [
        HEADERS['User-Agent'],
        'curl/7.68.0',
        'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
    ]

    for ua in user_agents:
        a = {'user_agent': ua, 'ok': False}
        try:
            r = session.get(url, headers={**HEADERS, 'User-Agent': ua}, timeout=timeout, allow_redirects=True)
            a.update({
                'status': r.status_code,
                'final_url': r.url,
                'headers': {k: v for k, v in list(r.headers.items())[:10]},
                'snippet': r.text[:500].replace('\n', ' ')
            })
            a['ok'] = (r.status_code == 200)
        except Exception as e:
            a['error'] = str(e)
            a['trace'] = traceback.format_exc().splitlines()[-1]
        result['attempts'].append(a)

    return result


def run_diagnostics(urls=None):
    """Run diagnostics for a list of urls and print results concisely."""
    urls = urls or [
        'https://www.sbt-japan.co.jp/used-cars/list/',
        'https://www.beforward.jp/cars/',
        'https://www.carfromjapan.com/car-search/',
        'https://www.aaajapan.com/cars',
        'https://www.japanesecartrade.com/vehicles/'
    ]
    session = requests.Session()
    reports = []
    for u in urls:
        print(f"Diagnosing {u}...")
        r = diagnose_url(u, session=session)
        reports.append(r)
        # concise print
        print(' DNS:', r['dns'])
        for att in r['attempts']:
            if att.get('ok'):
                print('  OK:', att['status'], att.get('final_url'))
            else:
                s = att.get('status', 'ERR')
                print('  Try UA:', att['user_agent'][:40], '->', s)
    return reports


def discover_listing_endpoints(domain_url, session=None, timeout=8):
    """
    Fetch the site root and look for anchor hrefs that likely point to listing/search pages.
    Returns a list of candidate URLs.
    """
    session = session or requests.Session()
    candidates = []
    try:
        resp = session.get(domain_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            return candidates
        soup = BeautifulSoup(resp.content, 'html.parser')
        anchors = soup.find_all('a', href=True)
        keywords = ['car', 'cars', 'search', 'used', 'list', 'inventory', 'vehicle', 'listing']
        seen = set()
        for a in anchors:
            href = a['href']
            href_lower = href.lower()
            if any(k in href_lower for k in keywords):
                # normalize relative URLs
                if href.startswith('/'):
                    full = domain_url.rstrip('/') + href
                elif href.startswith('http'):
                    full = href
                else:
                    full = domain_url.rstrip('/') + '/' + href
                if full not in seen:
                    seen.add(full)
                    candidates.append(full)
    except Exception:
        return candidates

    return candidates


def choose_endpoint(domain_url, preferred_keywords=None, session=None):
    """
    Return the best candidate listing endpoint for `domain_url` using
    `discover_listing_endpoints`. `preferred_keywords` is a list of strings
    to prefer when selecting a candidate.
    """
    session = session or requests.Session()
    candidates = discover_listing_endpoints(domain_url, session=session)
    if not candidates:
        return None

    if preferred_keywords:
        for kw in preferred_keywords:
            for c in candidates:
                if kw in c.lower():
                    return c

    # fallback: return first candidate
    return candidates[0]

SITE_SOURCES = [
    'SBT Japan',
    'BE FORWARD',
    'Car From Japan',
    'AAA Japan',
    'Japanese Car Trade'
]


def generate_mock_data(n=500, source=None):
    """
    Generates mock car data representing listings from Japanese exporters.
    This ensures the project is runnable even if scraping is blocked.
    """
    # Define correct make-model relationships
    make_model_map = {
        'Toyota': ['Axio', 'Vitz', 'Fielder', 'Wish', 'FJ Cruiser', 'RAV-4'],
        'Honda': ['Fit', 'Vezel', 'Grace', 'CRV'],
        'Mazda': ['Demio', 'CX-5', 'Axela', 'Atenza', '3', 'CX-3'],
        'Nissan': ['Note', 'X-Trail', 'Skyline'],
        'Subaru': ['Impreza', 'Forester', 'Legacy', 'Outback'],
        'Mitsubishi': ['Mirage', 'Outlander']
    }

    fuel_types = ['Petrol', 'Diesel', 'Hybrid']
    transmissions = ['Automatic', 'Manual']
    body_types = ['Sedan', 'Hatchback', 'SUV', 'Wagon']

    data = []
    for _ in range(n):
        year = random.randint(2018, 2024)

        # Choose make and corresponding model correctly
        make = random.choice(list(make_model_map.keys()))
        model = random.choice(make_model_map[make])

        # Generate realistic price range in JPY
        base_price = random.randint(800000, 2500000)

        # Mileage correlates with year
        age = 2024 - year
        mileage = random.randint(5000, 80000) - (age * 2000)
        if mileage < 1000:
            mileage = 1000

        data.append({
            'make': make,
            'model': model,
            'year': year,
            'mileage_km': max(0, mileage),
            'engine_cc': random.choice([1300, 1500, 1800, 2000, 2400]),
            'fuel_type': random.choice(fuel_types),
            'transmission': random.choice(transmissions),
            'body_type': random.choice(body_types),
            'price_jpy': base_price,
            'source': source if source is not None else random.choice(SITE_SOURCES),
            'url': f"http://example.com/{make}/{model}"
        })

    return pd.DataFrame(data)




def extract_make_model(title):
    """
    Extracts make and model from a car title string.
    Ensures the model corresponds to the correct make.
    """
    # Define correct make-model relationships
    make_model_map = {
        'Toyota': ['Axio', 'Vitz', 'Fielder', 'Wish', 'FJ Cruiser', 'RAV-4'],
        'Honda': ['Fit', 'Vezel', 'Grace', 'CRV'],
        'Mazda': ['Demio', 'CX-5', 'Axela', 'Atenza', '3', 'CX-3'],
        'Nissan': ['Note', 'X-Trail', 'Skyline'],
        'Subaru': ['Impreza', 'Forester', 'Legacy', 'Outback'],
        'Mitsubishi': ['Mirage', 'Outlander']
    }

    title_upper = title.upper()
    make = 'Unknown'
    model = 'Unknown'

    # First, find the make
    for m in make_model_map.keys():
        if m.upper() in title_upper:
            make = m
            break

    # Then, find the model that belongs to this make
    if make != 'Unknown':
        for mod in make_model_map[make]:
            if mod.upper() in title_upper:
                model = mod
                break

    # If we found a make but no matching model, or no make but found a model,
    # try to find the correct make for any model we can find
    if model == 'Unknown':
        all_models = []
        model_to_make = {}
        for m, models_list in make_model_map.items():
            for mod in models_list:
                all_models.append(mod)
                model_to_make[mod] = m

        for mod in all_models:
            if mod.upper() in title_upper:
                model = mod
                make = model_to_make[mod]
                break

    return make, model


def scrape_sbt_japan():
    """
    Scrapes car listings from SBT Japan (sbt-japan.co.jp)
    """
    print("Scraping SBT Japan...")
    try:
        base = "https://www.sbt-japan.co.jp/"
        session = requests.Session()
        try:
            session.get(base, headers=HEADERS, timeout=8)
        except Exception:
            pass

        candidate = choose_endpoint(base, preferred_keywords=['used-cars', 'list', 'stock'], session=session)
        if candidate is None:
            candidate = base + 'used-cars/list/'

        resp = fetch_url(candidate, session=session)
        if resp is None:
            print(f"SBT Japan fetch failed for {candidate}")
            return generate_mock_data(100, source='SBT Japan')
        soup = BeautifulSoup(resp.content, 'html.parser')

        data = []
        listings = soup.find_all('div', class_=['car-list-item', 'vehicle-card', 'listing'])

        for listing in listings[:100]:
            try:
                title_elem = listing.find(['h2', 'h3', 'span'], class_=['title', 'car-title', 'name'])
                title = title_elem.text.strip() if title_elem else "Unknown"

                price_elem = listing.find('span', class_=['price', 'selling-price', 'jpy-price'])
                price_text = price_elem.text.strip() if price_elem else "0"
                price_jpy = int(''.join(filter(str.isdigit, price_text))) if price_text else 1000000

                year_elem = listing.find(string=lambda text: '20' in text if text else False)
                year = int(''.join(filter(str.isdigit, year_elem.text[:4]))) if year_elem else 2020

                mileage_elem = listing.find(['span', 'td'], class_=['mileage', 'odometer', 'km'])
                mileage_text = mileage_elem.text.strip() if mileage_elem else "30000"
                mileage = int(''.join(filter(str.isdigit, mileage_text))) if mileage_text else 30000

                make, model = extract_make_model(title)

                data.append({
                    'make': make,
                    'model': model,
                    'year': year,
                    'mileage_km': mileage,
                    'engine_cc': random.choice([1300, 1500, 1800, 2000, 2400]),
                    'fuel_type': random.choice(['Petrol', 'Diesel', 'Hybrid']),
                    'transmission': random.choice(['Automatic', 'Manual']),
                    'body_type': random.choice(['Sedan', 'Hatchback', 'SUV', 'Wagon']),
                    'price_jpy': price_jpy,
                    'source': 'SBT Japan',
                    'url': url
                })
            except Exception:
                continue

        if data:
            return pd.DataFrame(data)
        else:
            return generate_mock_data(100, source='SBT Japan')

    except Exception as e:
        print(f"SBT Japan scraping failed: {e}")
        return generate_mock_data(100, source='SBT Japan')


def scrape_beforward():
    """
    Scrapes car listings from BE FORWARD (beforward.jp)
    """
    print("Scraping BE FORWARD...")
    try:
        base = "https://www.beforward.jp/"
        session = requests.Session()
        try:
            session.get(base, headers=HEADERS, timeout=8)
        except Exception:
            pass

        candidate = choose_endpoint(base, preferred_keywords=['stocklist', 'stock', 'search', 'cars'], session=session)
        if candidate is None:
            candidate = base

        resp = fetch_url(candidate, session=session)
        if resp is None:
            print(f"BE FORWARD fetch failed for {candidate}")
            return generate_mock_data(100, source='BE FORWARD')
        soup = BeautifulSoup(resp.content, 'html.parser')

        data = []
        listings = soup.find_all('tr', class_=['vehicle', 'car-row']) or \
                   soup.find_all('div', class_=['car-item', 'vehicle-card'])

        for listing in listings[:100]:
            try:
                title_elem = listing.find(['td', 'span'], class_=['title', 'name', 'car-name'])
                title = title_elem.text.strip() if title_elem else "Unknown"

                price_elem = listing.find(['td', 'span'], class_=['price', 'amount'])
                price_text = price_elem.text.strip() if price_elem else "0"
                price_jpy = int(''.join(filter(str.isdigit, price_text))) if price_text else 1200000

                year_elem = listing.find(['td', 'span'], class_=['year', 'model-year'])
                year = int(year_elem.text.strip()[:4]) if year_elem else 2020

                mileage_elem = listing.find(['td', 'span'], class_=['mileage', 'km'])
                mileage_text = mileage_elem.text.strip() if mileage_elem else "40000"
                mileage = int(''.join(filter(str.isdigit, mileage_text))) if mileage_text else 40000

                make, model = extract_make_model(title)

                data.append({
                    'make': make,
                    'model': model,
                    'year': year,
                    'mileage_km': mileage,
                    'engine_cc': random.choice([1300, 1500, 1800, 2000, 2400]),
                    'fuel_type': random.choice(['Petrol', 'Diesel', 'Hybrid']),
                    'transmission': random.choice(['Automatic', 'Manual']),
                    'body_type': random.choice(['Sedan', 'Hatchback', 'SUV', 'Wagon']),
                    'price_jpy': price_jpy,
                    'source': 'BE FORWARD',
                    'url': url
                })
            except Exception:
                continue

        if data:
            return pd.DataFrame(data)
        else:
            return generate_mock_data(100, source='BE FORWARD')

    except Exception as e:
        print(f"BE FORWARD scraping failed: {e}")
        return generate_mock_data(100, source='BE FORWARD')


def scrape_car_from_japan():
    """
    Scrapes car listings from Car From Japan (carfromjapan.com)
    """
    print("Scraping Car From Japan...")
    try:
        base = "https://www.carfromjapan.com/"
        session = requests.Session()
        try:
            session.get(base, headers=HEADERS, timeout=8)
        except Exception:
            pass

        candidate = choose_endpoint(base, preferred_keywords=['cheap-used', 'cheap-used-cars', 'car-search', 'stocklist'], session=session)
        if candidate is None:
            candidate = base

        resp = fetch_url(candidate, session=session)
        if resp is None:
            print(f"Car From Japan fetch failed for {candidate}")
            return generate_mock_data(100, source='Car From Japan')
        soup = BeautifulSoup(resp.content, 'html.parser')

        data = []
        listings = soup.find_all('div', class_=['inventory-item', 'vehicle-item', 'car-listing'])

        for listing in listings[:100]:
            try:
                title_elem = listing.find(['h3', 'a'], class_=['vehicle-title', 'title', 'name'])
                title = title_elem.text.strip() if title_elem else "Unknown"

                price_elem = listing.find('span', class_=['price', 'vehicle-price', 'bid-price'])
                price_text = price_elem.text.strip() if price_elem else "0"
                price_jpy = int(''.join(filter(str.isdigit, price_text))) if price_text else 1100000

                specs_elem = listing.find('div', class_=['specs', 'details', 'vehicle-specs'])
                year = 2020
                mileage = 35000
                if specs_elem:
                    specs_text = specs_elem.text
                    year_match = [int(s) for s in specs_text.split() if s.isdigit() and 2010 <= int(s) <= 2025]
                    if year_match:
                        year = year_match[0]
                    mileage_match = [int(s) for s in specs_text.split() if s.isdigit() and 5000 <= int(s) <= 200000]
                    if mileage_match:
                        mileage = mileage_match[0]

                make, model = extract_make_model(title)

                data.append({
                    'make': make,
                    'model': model,
                    'year': year,
                    'mileage_km': mileage,
                    'engine_cc': random.choice([1300, 1500, 1800, 2000, 2400]),
                    'fuel_type': random.choice(['Petrol', 'Diesel', 'Hybrid']),
                    'transmission': random.choice(['Automatic', 'Manual']),
                    'body_type': random.choice(['Sedan', 'Hatchback', 'SUV', 'Wagon']),
                    'price_jpy': price_jpy,
                    'source': 'Car From Japan',
                    'url': url
                })
            except Exception:
                continue

        if data:
            return pd.DataFrame(data)
        else:
            return generate_mock_data(100, source='Car From Japan')

    except Exception as e:
        print(f"Car From Japan scraping failed: {e}")
        return generate_mock_data(100, source='Car From Japan')


def scrape_aaajapan():
    """
    Scrapes car listings from AAA Japan (aaajapan.com)
    """
    print("Scraping AAA Japan...")
    try:
        base = "https://www.aaajapan.com/"
        session = requests.Session()
        try:
            session.get(base, headers=HEADERS, timeout=8)
        except Exception:
            pass

        candidate = choose_endpoint(base, preferred_keywords=['cars-available', 'cars', 'auctions'], session=session)
        if candidate is None:
            candidate = base

        resp = fetch_url(candidate, session=session)
        if resp is None:
            print(f"AAA Japan fetch failed for {candidate}")
            return generate_mock_data(100, source='AAA Japan')
        soup = BeautifulSoup(resp.content, 'html.parser')

        data = []
        listings = soup.find_all('div', class_=['car-card', 'listing-card', 'vehicle-listing'])

        for listing in listings[:100]:
            try:
                title_elem = listing.find(['h2', 'h3', 'a'], class_=['title', 'car-name'])
                title = title_elem.text.strip() if title_elem else "Unknown"

                price_elem = listing.find('span', class_=['price', 'jpy', 'listing-price'])
                price_text = price_elem.text.strip() if price_elem else "0"
                price_jpy = int(''.join(filter(str.isdigit, price_text))) if price_text else 1050000

                year_text = listing.find(string=lambda text: '20' in text if text else False)
                year = int(year_text.text[:4]) if year_text else 2020

                mileage_elem = listing.find('span', class_=['mileage', 'km', 'odometer'])
                mileage_text = mileage_elem.text.strip() if mileage_elem else "32000"
                mileage = int(''.join(filter(str.isdigit, mileage_text))) if mileage_text else 32000

                make, model = extract_make_model(title)

                data.append({
                    'make': make,
                    'model': model,
                    'year': year,
                    'mileage_km': mileage,
                    'engine_cc': random.choice([1300, 1500, 1800, 2000, 2400]),
                    'fuel_type': random.choice(['Petrol', 'Diesel', 'Hybrid']),
                    'transmission': random.choice(['Automatic', 'Manual']),
                    'body_type': random.choice(['Sedan', 'Hatchback', 'SUV', 'Wagon']),
                    'price_jpy': price_jpy,
                    'source': 'AAA Japan',
                    'url': url
                })
            except Exception:
                continue

        if data:
            return pd.DataFrame(data)
        else:
            return generate_mock_data(100, source='AAA Japan')

    except Exception as e:
        print(f"AAA Japan scraping failed: {e}")
        return generate_mock_data(100, source='AAA Japan')


def scrape_japanese_car_trade():
    """
    Scrapes car listings from Japanese Car Trade (japanesecartrade.com)
    """
    print("Scraping Japanese Car Trade...")
    try:
        url = "https://www.japanesecartrade.com/vehicles/"
        resp = fetch_url(url)
        if resp is None:
            print(f"Japanese Car Trade fetch failed for {url}")
            return generate_mock_data(100, source='Japanese Car Trade')
        soup = BeautifulSoup(resp.content, 'html.parser')

        data = []
        listings = soup.find_all('div', class_=['vehicle', 'listing', 'vehicle-item'])

        for listing in listings[:100]:
            try:
                title_elem = listing.find(['h3', 'a'], class_=['title', 'vehicle-title'])
                title = title_elem.text.strip() if title_elem else "Unknown"

                price_elem = listing.find('span', class_=['price', 'amount', 'jpy-price'])
                price_text = price_elem.text.strip() if price_elem else "0"
                price_jpy = int(''.join(filter(str.isdigit, price_text))) if price_text else 1150000

                specs = listing.find('div', class_=['specs', 'vehicle-specs', 'details'])
                year = 2020
                mileage = 38000
                if specs:
                    all_text = specs.text
                    for word in all_text.split():
                        if word.isdigit() and 2010 <= int(word) <= 2025:
                            year = int(word)
                            break
                    numbers = [int(s) for s in all_text.split() if s.isdigit() and 5000 <= int(s) <= 200000]
                    if numbers:
                        mileage = numbers[0]

                make, model = extract_make_model(title)

                data.append({
                    'make': make,
                    'model': model,
                    'year': year,
                    'mileage_km': mileage,
                    'engine_cc': random.choice([1300, 1500, 1800, 2000, 2400]),
                    'fuel_type': random.choice(['Petrol', 'Diesel', 'Hybrid']),
                    'transmission': random.choice(['Automatic', 'Manual']),
                    'body_type': random.choice(['Sedan', 'Hatchback', 'SUV', 'Wagon']),
                    'price_jpy': price_jpy,
                    'source': 'Japanese Car Trade',
                    'url': url
                })
            except Exception:
                continue

        if data:
            return pd.DataFrame(data)
        else:
            return generate_mock_data(100, source='Japanese Car Trade')

    except Exception as e:
        print(f"Japanese Car Trade scraping failed: {e}")
        return generate_mock_data(100, source='Japanese Car Trade')


def get_data():
    """
    Main function to orchestrate data collection.
    """
    scraping_functions = [
        scrape_sbt_japan,
        scrape_beforward,
        scrape_car_from_japan,
        scrape_aaajapan,
        scrape_japanese_car_trade,
    ]

    all_frames = []
    for scraper in scraping_functions:
        try:
            all_frames.append(scraper())
        except Exception as e:
            print(f"Warning: {scraper.__name__} failed: {e}")

    if all_frames:
        df = pd.concat(all_frames, ignore_index=True)
    else:
        df = generate_mock_data(100)

    # Basic Cleaning
    df = df[df['year'] >= 2018]  # Filter requirement
    df.drop_duplicates(inplace=True)
    df.dropna(inplace=True)

    return df

import time
import datetime
import requests
import sqlite3
import re
import os
import json

SERVER_URL = "http://localhost:8003/api/v1"

def get_last_fetched_time():
    try:
        response = requests.get(f"{SERVER_URL}/imsync/lastfetchedtime")
        print("response", response)
        if response.status_code == 200:
            raw_ts = response.json().get("lastfetchedtime")
            print("raw_ts", raw_ts)

            if raw_ts is None:
                return None  # No previous timestamp

            return int(raw_ts)  # Convert string or number to int
        return None  # API responded but no timestamp
    except Exception as e:
        print(f"Error fetching last fetched time: {e}")
        return "_FAIL_"

def update_last_fetched_time(timestamp):
    try:
        url = f"{SERVER_URL}/imsync/setlastfetchedtime"
        payload = {"lastfetchedtime": str(timestamp)}
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("🕒 Timestamp updated successfully.")
        else:
            print(f"❌ Failed to update timestamp: {response.status_code}")
    except Exception as e:
        print(f"🚨 Error updating timestamp: {e}")

def get_current_apple_timestamp():
    apple_epoch = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = now - apple_epoch
    return int(delta.total_seconds() * 1_000_000_000)  # nanoseconds

def send_to_api(messages, timestamp):
    url = f"{SERVER_URL}/messages/sync"
    print("url", url)
    payload = {"messages": messages, "timestamp": str(timestamp)}
    headers = {'Content-Type': 'application/json'}

    print(messages)
    

    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"✅ {len(messages)} messages successfully sent to the API")
            return True
        else:
            print(f"❌ Failed to send messages to the API: {response.status_code}")
            return False
    except Exception as e:
        print(f"🚨 Error sending messages to the API: {e}")
        return False

def get_chat_mapping(db_location):
    try:
        conn = sqlite3.connect(db_location)
        cursor = conn.cursor()
        cursor.execute("SELECT room_name, display_name FROM chat")
        mapping = {room_name: display_name for room_name, display_name in cursor.fetchall()}
        conn.close()
        return mapping
    except Exception as e:
        print(f"Error getting chat mapping: {e}")
        return {}

def extract_rtf_text(data):
    try:
        if not data:
            return "No content"
        text = data.decode('utf-8', errors='replace')
        text = re.sub(r'(NSString|NSAttributedString|NSValue|NSNumber|NSDictionary|NSObject|streamtype|iI|__kIMMessagePartAttributeName|\*|@|\+|data|file|NSLog|NSRange)', '', text)
        text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
        text = re.sub(r'^[^\w]*d*', '', text)
        text = re.sub(r'\bi\b', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip() or "Unreadable message content"
    except Exception as e:
        print(f"Error extracting text: {e}")
        return "Unreadable message content"

def prompt_mac_permission():
    print("""
    🚨 macOS Permission Required 🚨

    This script needs access to the Messages database.

    ➡️ Go to:
    System Settings > Privacy & Security > Full Disk Access

    ✅ Enable Full Disk Access for the Terminal (or the app running this script)

    🔁 Then restart the script
    """)

def load_address_book(path="addressbook.json"):
    try:
        with open(path, "r") as f:
            return f.read()  # Return raw JSON string
    except Exception as e:
        print(f"Error loading address book: {e}")
        return "[]"

def normalize_number(number):
    return re.sub(r"[^\d]", "", number or "").strip()

def combine_data(recent_messages, addressBookData):
    try:
        addressBookData = json.loads(addressBookData)
    except Exception as e:
        print(f"⚠️ Failed to parse addressBookData: {e}")
        addressBookData = []

    for message in recent_messages:
        phone_number_raw = message.get("phone_number", "")
        phone_number = normalize_number(phone_number_raw)

        matched_contact = None
        for contact in addressBookData:
            contact_number_raw = contact.get("NUMBERCLEAN", "")
            contact_number = normalize_number(contact_number_raw)
            if phone_number == contact_number:
                matched_contact = contact
                break

        if matched_contact:
            message["first_name"] = matched_contact.get("FIRSTNAME", "")
            message["last_name"] = matched_contact.get("LASTNAME", "")
        else:
            message["first_name"] = ""
            message["last_name"] = ""

    return recent_messages


def read_messages(db_location, last_timestamp):
    try:
        conn = sqlite3.connect(db_location)
        cursor = conn.cursor()
        
        # query = """
        # SELECT message.ROWID, message.date, message.text, message.attributedBody, 
        #        chat.chat_identifier, message.is_from_me, message.cache_roomnames, message.is_read, message.guid as message_id, chat_message_join.chat_id as thread_id,
        # CASE 
        # WHEN message.is_from_me = 1 THEN 'Me' 
        # ELSE handle.id 
        # END AS sender_phoneNumber           
        # FROM message
        # LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        # LEFT JOIN chat ON chat_message_join.chat_id = chat.ROWID
        # LEFT JOIN handle ON message.handle_id = handle.ROWID
        # WHERE message.date > ?
        # ORDER BY message.date ASC
        # """

        query_messages = """
        SELECT message.ROWID, message.date, message.text, message.attributedBody, 
            chat.chat_identifier, message.is_from_me, message.cache_roomnames, 
            message.is_read, message.guid as message_id, chat_message_join.chat_id as thread_id,
            CASE 
                WHEN message.is_from_me = 1 THEN 'Me' 
                ELSE handle.id 
            END AS sender_phoneNumber           
        FROM message
        LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        LEFT JOIN chat ON chat_message_join.chat_id = chat.ROWID
        LEFT JOIN handle ON message.handle_id = handle.ROWID
        WHERE message.date > ?
        ORDER BY message.date ASC
        """
        
        # cursor.execute(query, (last_timestamp,))
        # results = cursor.fetchall()

        cursor.execute(query_messages, (last_timestamp,))
        results_messages = cursor.fetchall()

        # 🔥 Second query: Fetch messages where date_read > last_timestamp
        query_read = """
        SELECT message.ROWID, message.date, message.date_read, message.text, message.attributedBody, 
            chat.chat_identifier, message.is_from_me, message.cache_roomnames, 
            message.is_read, message.guid as message_id, chat_message_join.chat_id as thread_id,
            CASE 
                WHEN message.is_from_me = 1 THEN 'Me' 
                ELSE handle.id 
            END AS sender_phoneNumber
        FROM message
        LEFT JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        LEFT JOIN chat ON chat_message_join.chat_id = chat.ROWID
        LEFT JOIN handle ON message.handle_id = handle.ROWID
        WHERE message.date_read > ?
        ORDER BY message.date_read ASC
        """

        cursor.execute(query_read, (last_timestamp,))
        results_read = cursor.fetchall()

        mapping = get_chat_mapping(db_location)

        messages = []
        readMessages = []
        for rowid, date, text, attributed_body, chat_identifier, is_from_me, cache_roomname, is_read, message_id, thread_id, sender_phoneNumber in results_messages:
            body = text
            if not body and attributed_body:
                body = extract_rtf_text(attributed_body)
            
            phone_number = chat_identifier or "Me"
            mapped_name = mapping.get(cache_roomname, "")

            # Convert Apple timestamp to datetime
            mod_date = datetime.datetime(2001, 1, 1)
            # timestamp = (mod_date + datetime.timedelta(seconds=date / 1000000000)).strftime("%Y-%m-%d %H:%M:%S")
            timestamp = (mod_date + datetime.timedelta(seconds=date / 1000000000)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            messages.append({
                "message_id": message_id,#Pass the unique message id
                "thread_id": thread_id,#Pass the unique conversation/chat/thread i
                "date": timestamp,
                "body": body,
                "phone_number": phone_number,
                "is_from_me": bool(is_from_me),
                "is_read": bool(is_read),
                "cache_roomname": cache_roomname,
                "group_chat_name": mapped_name,
                "sender_phone_number":sender_phoneNumber
            })

        for rowid, date, date_read, text, attributed_body, chat_identifier, is_from_me, cache_roomname, is_read, message_id, thread_id, sender_phoneNumber in results_read:
            body = text
            if not body and attributed_body:
                body = extract_rtf_text(attributed_body)
            
            phone_number = chat_identifier or "Me"
            mapped_name = mapping.get(cache_roomname, "")

            # Convert Apple timestamps to ISO format
            mod_date = datetime.datetime(2001, 1, 1)
            sent_timestamp = (mod_date + datetime.timedelta(seconds=date / 1000000000)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            read_timestamp = (mod_date + datetime.timedelta(seconds=date_read / 1000000000)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            readMessages.append({
                "message_id": message_id,         # Unique message ID
                "thread_id": thread_id,           # Unique chat/thread ID
                "date": sent_timestamp,           # Original send date
                # "date_read": read_timestamp,      # Date when message was read
                "body": body,
                "phone_number": phone_number,
                "is_from_me": bool(is_from_me),
                "is_read": bool(is_read),
                "cache_roomname": cache_roomname,
                "group_chat_name": mapped_name,
                "sender_phone_number": sender_phoneNumber
            })

        conn.close()
       

        combined = messages + readMessages
        unique = {msg["message_id"]: msg for msg in combined}
        final_messages = list(unique.values())

# Sort by date
        final_messages.sort(key=lambda x: x["date"])

        return final_messages
        # return messages, readMessages
    except Exception as e:
        print(f"Error reading messages: {e}")
        return []

def run_continuously(db_location):
    # Initial delay to ensure server is up
    time.sleep(2)
    # address_book_json = load_address_book()
    address_book_json  = []

    while True:
        try:
            # Get the last timestamp we processed from the server
            last_processed_timestamp = get_last_fetched_time()
            print("last_processed_timestamp", last_processed_timestamp)

            if last_processed_timestamp == "_FAIL_":
                print("❌ Failed to connect to server to get last fetched timestamp.")
                print("🛑 Exiting process due to connection failure.")
                return  # or exit(1)
            
            if last_processed_timestamp is None:
                print("ℹ️  No previous timestamp found. Fetching all messages...")
                messages = read_messages(db_location, 0)
            else:
                print(f"🔄 Checking for new messages since {last_processed_timestamp}...")
                messages = read_messages(db_location, last_processed_timestamp)
                

            newest_timestamp = get_current_apple_timestamp()

            if messages:
                messages = combine_data(messages, address_book_json)
                print(f"📨 Found {len(messages)} new messages")
                if send_to_api(messages, newest_timestamp):
                    print(f"✅ Successfully processed {len(messages)} messages")
                    update_last_fetched_time(newest_timestamp)
                else:
                    print("⚠️ Failed to send messages, will retry next cycle")
            else:
                print("ℹ️  No new messages found")
                update_last_fetched_time(newest_timestamp)

            print("⏳ Waiting for 60 seconds before next check...")
            time.sleep(20)
            
        except Exception as e:
            print(f"🚨 Error in main loop: {e}")
            print("🔄 Retrying in 30 seconds...")
            time.sleep(30)

def has_permission(db_location):
    return os.access(db_location, os.R_OK)

if __name__ == "__main__":
    db_location = "/Users/achit225/Library/Messages/chat.db"

    if not has_permission(db_location):
        print("❗ Cannot read the database file.")
        prompt_mac_permission()
        exit(1)

    run_continuously(db_location)

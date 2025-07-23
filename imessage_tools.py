import time
import datetime
import requests
import sqlite3
import re
import os
import json
import plistlib
import zlib

import logging
from logging.handlers import RotatingFileHandler

# ----------------- Logging Setup -----------------
LOG_FILE = "im_sync.log"
logger = logging.getLogger("iMessageSync")
logger.setLevel(logging.INFO)

# Rotating log file: 5 MB per file, keep 3 backups
handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Also log to console
console = logging.StreamHandler()
console.setFormatter(formatter)
logger.addHandler(console)
# --------------------------------------------------



SERVER_URL = "http://localhost:8003/api/v1"
# SERVER_URL = "https://sidekick-8e72374da48d.herokuapp.com/api/v1"
AUTH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6MTAsImVtYWlsIjoiYXJhY2NhM0BnbWFpbC5jb20iLCJyb2xlIjoiYWRtaW4iLCJpYXQiOjE3NTI0OTM2MTAsImV4cCI6MjA2ODA2OTYxMH0.WOYXqxzkDE_cyvM2qCL0yvgq50f9STIsWiHTirSUDpM"

def get_last_fetched_time():
    try:
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {AUTH_TOKEN}'}
        response = requests.get(f"{SERVER_URL}/imsync/lastfetchedtime", headers=headers)
        logger.info(f"Response from lastfetchedtime API: {response.status_code}")
        
        if response.status_code == 200:
            raw_ts = response.json().get("lastfetchedtime")
            print("raw_ts", raw_ts)

            if raw_ts is None:
                return None  # No previous timestamp

            return int(raw_ts)  # Convert string or number to int
        return None  # API responded but no timestamp
    except requests.exceptions.RequestException as e:
        print(f"Error fetching last fetched time: {e}")
        logger.error(f"Error fetching last fetched time: {e}")
        return None
    except Exception as e:
         print(f"🚨 Unexpected error fetching last fetched time: {e}")
         logger.error("Unexpected error fetching last fetched time: {e}")
         return None  # Gracefully ha


def update_last_fetched_time(timestamp):
    try:
        url = f"{SERVER_URL}/imsync/setlastfetchedtime"
        payload = {"lastfetchedtime": str(timestamp)}
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {AUTH_TOKEN}'}
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            print("🕒 Timestamp updated successfully.")
            logger.info("Last fetch timestamp updated successfully.")
        else:
            print(f"❌ Failed to update timestamp: {response.status_code}")
            logger.error(f"Failed to update timestamp: {response.status_code}")
    except Exception as e:
        print(f"🚨 Error updating timestamp: {e}")
        logger.error(f"Error updating timestamp: {e}")

def get_current_apple_timestamp():
    apple_epoch = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = now - apple_epoch
    return int(delta.total_seconds() * 1_000_000_000)  # nanoseconds

def send_to_api(messages, timestamp):
    # return True
    url = f"{SERVER_URL}/imsync/sync"
    print("url", url)
    payload = {"messages": messages, "timestamp": str(timestamp)}
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {AUTH_TOKEN}'}

    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 201:
            print(f"✅ {len(messages)} messages successfully sent to the API")
            logger.info("Messages successfully sent to the API")
            return True
        else:
            print(f"❌ Failed to send messages to the API: {response.status_code}")
            logger.info(f"Failed to send messages to the API: {response.status_code}")
            return False
    except Exception as e:
        print(f"🚨 Error sending messages to the API: {e}")
        logger.info(f"Error sending messages to the API: {e}")
        
        return False

def send_delete_thread_to_api(deletedMessages, timestamp):
    # Extract unique thread_ids
    unique_thread_ids = list({msg['thread_id'] for msg in deletedMessages if msg['thread_id'] is not None})

    # Create payload with array of objects
    payload = {
        "messages": [{"thread_id": thread_id} for thread_id in unique_thread_ids],
        "timestamp": str(timestamp)
    }

    url = f"{SERVER_URL}/imsync/remove"
    print("url", url)
    print("Payload to API:", json.dumps(payload, indent=2))

    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {AUTH_TOKEN}'}

    try:
        response = requests.put(url, json=payload, headers=headers)
        if response.status_code == 201:
            print(f"✅ Deleted thread IDs sent successfully: {unique_thread_ids}")
            logger.info("Deleted thread IDs sent successfully")
            return True
        else:
            print(f"❌ Failed to send deleted thread IDs: {response.status_code}")
            logger.error(f"Failed to send deleted thread IDs: {response.status_code}")
            return False
    except Exception as e:
        print(f"🚨 Error sending deleted thread IDs: {e}")
        logger.error(f"Error sending deleted thread IDs: {e}")
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

def extract_rtf_text01(data):
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



def extract_rtf_text(data):
    try:
        if not data:
            return "No content"
        
        # Try to parse as a binary plist
        try:
            plist = plistlib.loads(data)
            # Extract NS.string if present (common for message text)
            if isinstance(plist, dict) and "NS.string" in plist:
                text = plist["NS.string"]
                if text:
                    # Clean up any extra whitespace
                    text = re.sub(r'\s+', ' ', text).strip()
                    return text or "Unreadable message content"
            
            # Handle calendar event attributes (e.g., kIMCalendarEventAttributeName)
            if isinstance(plist, dict) and any(key.endswith("kIMCalendarEventAttributeName") for key in plist):
                # Attempt to extract relevant text (e.g., time or event details)
                for key, value in plist.items():
                    if isinstance(value, str) and re.match(r'\d{1,2}:\d{2}', value):
                        return value  # Return time like "12:24" if found
                    elif isinstance(value, str) and value.strip():
                        return value.strip()  # Return any other valid string
                return "Calendar event detected"
        
        except plistlib.InvalidFileException:
            # Fallback: Try decoding as UTF-8 for non-plist data
            try:
                text = data.decode('utf-8', errors='replace')
                # Clean up common plist-like artifacts, but preserve time formats
                text = re.sub(r'(NSString|NSAttributedString|NSValue|NSNumber|NSDictionary|NSObject|streamtype|iI|__kIMMessagePartAttributeName|\*|\+)', '', text)
                text = re.sub(r'[^a-zA-Z0-9\s@:.,-]', '', text)  # Preserve time format (e.g., 12:24)
                text = re.sub(r'\s+', ' ', text).strip()
                if "kIMDataDetectedAttributeName" in text:
                       print("🔪 Trimming at kIMDataDetectedAttributeName")
                       text = text.split("kIMDataDetectedAttributeName")[0].strip()


                
                return text or "Unreadable message content"
            except Exception:
                return "Unreadable message content"
        
        return "Unreadable message content"
    
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
        logger.error(f"Failed to parse addressBookData: {e}")
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
                print("Raw", body)
                

            if body: 
                body = body.replace("NSMutableAttributedString", "")
                body = body.replace("NSMutableString", "")
                body = body.strip()


                if body.startswith('d'):
                    body = body[1:].strip()

                # 📂 Remove ikIMFileTransferGUIDAttributeName and everything after
                if "ikIMFileTransferGUIDAttributeName" in body:
                    print("📂 Attachment detected - removing GUID and everything after")
                    body = re.sub(r'ikIMFileTransferGUIDAttributeName.*', '', body).strip()

                # 📆 Remove kIMCalendarEventAttributeName and everything after
                if "kIMCalendarEventAttributeName" in body:
                    print("📆 Calendar event detected - cleaning up")
                    body = re.sub(r'kIMCalendarEventAttributeName.*', '', body).strip()

                if "kIMDataDetectedAttributeName" in body:
                       print("🔪 Trimming at kIMDataDetectedAttributeName")
                       body = body.split("kIMDataDetectedAttributeName")[0].strip()    
                 
                # 📞 Remove kIMPhoneNumberAttributeName and everything after
                if "kIMPhoneNumberAttributeName" in body:
                        print("📞 Phone number detected - cleaning up")
                        body = re.sub(r'kIMPhoneNumberAttributeName.*', '', body).strip()       

                # Final cleanup: remove any trailing "i" left alone at end
                body = re.sub(r'\s+i$', '', body).strip()
                body = re.sub(r'^\s*@\s*', '', body).strip()

                print("✅ Exact extracted body:", body)
            if not body or body.strip() == "":
              logger.info(f"⏭️ Skipping message with empty body (message_id={message_id})")
              continue  # Skip this iteration
            
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
                "date_read":timestamp,
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
                print("Raw", body)
                

            if body: 
                body = body.replace("NSMutableAttributedString", "")
                body = body.replace("NSMutableString", "")
                body = body.strip()


                if body.startswith('d'):
                    body = body[1:].strip()

                # 📂 Remove ikIMFileTransferGUIDAttributeName and everything after
                if "ikIMFileTransferGUIDAttributeName" in body:
                    print("📂 Attachment detected - removing GUID and everything after")
                    body = re.sub(r'ikIMFileTransferGUIDAttributeName.*', '', body).strip()

                # 📆 Remove kIMCalendarEventAttributeName and everything after
                if "kIMCalendarEventAttributeName" in body:
                    print("📆 Calendar event detected - cleaning up")
                    body = re.sub(r'kIMCalendarEventAttributeName.*', '', body).strip()

                if "kIMDataDetectedAttributeName" in body:
                       print("🔪 Trimming at kIMDataDetectedAttributeName")
                       body = body.split("kIMDataDetectedAttributeName")[0].strip()    

                # Final cleanup: remove any trailing "i" left alone at end
                body = re.sub(r'\s+i$', '', body).strip()

                print("✅ Exact extracted body:", body)
            if not body or body.strip() == "":
              logger.info(f"⏭️ Skipping message with empty body (message_id={message_id})")
              continue  # Skip this iteration
            
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
                "date_read": read_timestamp,      # Date when message was read
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
        logger.error(f"Error reading messages: {e}")
        return []

def read_deleted_messages(db_location, last_timestamp):
    try:
        conn = sqlite3.connect(db_location)
        cursor = conn.cursor()

        query_deleted = """
        SELECT crm.message_id, crm.delete_date, m.text, m.attributedBody, 
               chat.chat_identifier, m.is_from_me, m.cache_roomnames, 
               m.is_read, m.guid as original_message_guid, 
               crm.chat_id as thread_id,
               CASE 
                   WHEN m.is_from_me = 1 THEN 'Me' 
                   ELSE handle.id 
               END AS sender_phoneNumber
        FROM chat_recoverable_message_join crm
        LEFT JOIN message m ON crm.message_id = m.ROWID
        LEFT JOIN chat ON crm.chat_id = chat.ROWID
        LEFT JOIN handle ON m.handle_id = handle.ROWID
        WHERE crm.delete_date > ?
        ORDER BY crm.delete_date ASC
        """

        cursor.execute(query_deleted, (last_timestamp,))
        results_deleted = cursor.fetchall()
        conn.close()

        deletedMessages = []
        for message_id, delete_date, text, attributed_body, chat_identifier, is_from_me, cache_roomname, is_read, original_message_guid, thread_id, sender_phoneNumber in results_deleted:
            body = text or extract_rtf_text(attributed_body)
            if body:
                body = body.replace("NSMutableAttributedString", "").replace("NSMutableString", "").strip()
            else:
                body = "Message content not recoverable"
                
                
            
             

            deletedMessages.append({
                "message_id": message_id,
                "original_message_guid": original_message_guid,
                "thread_id": thread_id,
                "delete_date": delete_date,
                "body": body,
                "phone_number": chat_identifier or "Me",
                "is_from_me": bool(is_from_me),
                "is_read": bool(is_read),
                "cache_roomname": cache_roomname,
                "group_chat_name": cache_roomname,  # You can use mapping if needed
                "sender_phone_number": sender_phoneNumber
            })

        return deletedMessages

    except Exception as e:
        print(f"🚨 Error reading deleted messages: {e}")
        logger.error(f"Error reading deleted messages: {e}")
        return []

def run_continuously(db_location):
    # Initial delay to ensure server is up
    time.sleep(2)
    address_book_json = load_address_book()
    # address_book_json  = []

    while True:
        try:
            # Get the last timestamp we processed from the server
            last_processed_timestamp = get_last_fetched_time()
            print("last_processed_timestamp", last_processed_timestamp)
            logger.info(f"last_processed_timestamp: {last_processed_timestamp}")

            if last_processed_timestamp == "_FAIL_":
                print("❌ Failed to connect to server to get last fetched timestamp.")
                print("🛑 Exiting process due to connection failure.")
                logger.error("Failed to connect to server to get last fetched timestamp.")
                logger.error("Exiting process due to connection failure.")
                return  # or exit(1)
            
            if last_processed_timestamp is None:
                print("ℹ️  No previous timestamp found. Fetching all messages...")
                logger.info("No previous timestamp found. Fetching all messages...")
                messages = read_messages(db_location, 0)
                deleted_messages = read_deleted_messages(db_location, last_processed_timestamp)
            else:
                print(f"🔄 Checking for new messages since {last_processed_timestamp}...")
                logger.info(f"Checking for new messages since {last_processed_timestamp}..")
                messages = read_messages(db_location, last_processed_timestamp)
                deleted_messages = read_deleted_messages(db_location, last_processed_timestamp)
                

            newest_timestamp = get_current_apple_timestamp()

            print("DELETE", deleted_messages)


            if deleted_messages:
               print(f"🗑 Found {len(deleted_messages)} deleted messages")
               logger.info(f"Found {len(deleted_messages)} deleted messages")

               if send_delete_thread_to_api(deleted_messages, newest_timestamp):
                print("✅ Deleted thread IDs sent to API")
                logger.info("Deleted thread IDs sent to API")
               else:
                  print("⚠️ Failed to send deleted thread IDs to API")
                  logger.error("Failed to send deleted thread IDs to API")
            else:
                print("ℹ️ No deleted messages found.")
                logger.info("No deleted messages found.")



            if messages:
                messages = combine_data(messages, address_book_json)
                print(f"📨 Found {len(messages)} new messages")
                logger.info(f"Found {len(messages)} new messages")

                chunk_size = 100
                all_sent_successfully = True
                num_chunks = (len(messages) + chunk_size - 1) // chunk_size

                for i in range(0, len(messages), chunk_size):
                    chunk = messages[i:i + chunk_size]
                    current_chunk_num = (i // chunk_size) + 1
                    print(f"📦 Sending chunk {current_chunk_num}/{num_chunks} with {len(chunk)} messages...")
                    logger.info(f"Sending chunk {current_chunk_num}/{num_chunks} with {len(chunk)} messages...")
                    if not send_to_api(chunk, newest_timestamp):
                        print("⚠️ Failed to send chunk, will retry next cycle")
                        all_sent_successfully = False
                        break
                
                if all_sent_successfully:
                    print(f"✅ Successfully processed {len(messages)} messages")
                    logger.info(f"Successfully processed {len(messages)} messages")
                    update_last_fetched_time(newest_timestamp)
                else:
                    print("⚠️ Not all message chunks were sent successfully, will retry next cycle")
                    logger.info("Not all message chunks were sent successfully, will retry next cycle")
            else:
                print("ℹ️  No new messages found")
                logger.info("No new messages found")
                update_last_fetched_time(newest_timestamp)

            print("⏳ Waiting for 60 seconds before next check...")
            time.sleep(10)

            
            
        except Exception as e:
            print(f"🚨 Error in main loop: {e}")
            print("🔄 Retrying in 30 seconds...")
            logger.error("Error in main loop: {e}")
            logger.error("Retrying in 30 seconds...")

            time.sleep(30)

def has_permission(db_location):
    return os.access(db_location, os.R_OK)

if __name__ == "__main__":
    db_location = "/Users/achit225/Library/Messages/chat.db"

    if not has_permission(db_location):
        print("❗ Cannot read the database file.")
        logger.error("Cannot read the database file.")
        prompt_mac_permission()
        exit(1)

    run_continuously(db_location)

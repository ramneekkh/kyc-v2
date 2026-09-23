import logging
import time
from backend.search.utils import extract_entities_from_bulk_data

# Configure logging to see the output
logging.basicConfig(level=logging.INFO)

def test_bulk_extraction():
    print("Testing bulk entity extraction...")
    
    # Create dummy articles
    articles = [
        {
            "title": "Article 1",
            "url": "http://example.com/1",
            "content": "John Doe is the CEO of Acme Corp. He lives in New York."
        },
        {
            "title": "Article 2",
            "url": "http://example.com/2",
            "content": "Acme Corp announced a new partnership with Beta Ltd. Beta Ltd is based in London."
        },
        {
            "title": "Article 3",
            "url": "http://example.com/3",
            "content": "Jane Smith was appointed as the CTO of Acme Corp."
        }
    ]
    
    start_time = time.time()
    try:
        result = extract_entities_from_bulk_data(articles)
        # print(f"Extraction result: {result}")
        if result and 'nodes' in result:
            print("Extracted Nodes:")
            for node in result['nodes']:
                print(f"ID: {node.get('id')}, Label: {node.get('label')}, Group: {node.get('group')}")
    except Exception as e:
        print(f"Extraction failed with error: {e}")
    
    end_time = time.time()
    print(f"Time taken: {end_time - start_time:.2f} seconds")

if __name__ == "__main__":
    test_bulk_extraction()

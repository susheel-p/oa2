import socket
import sys

def test_bind(reuse_addr, exclusive_addr_use=False):
    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if reuse_addr:
        s1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if exclusive_addr_use:
        # exclusive address use is 22 (socket.SO_EXCLUSIVEADDRUSE)
        s1.setsockopt(socket.SOL_SOCKET, 22, 1)
    
    try:
        s1.bind(("127.0.0.1", 18501))
        s1.listen(1)
        print(f"s1 bound successfully (reuse_addr={reuse_addr}, exclusive={exclusive_addr_use})")
    except Exception as e:
        print("s1 failed to bind:", e)
        s1.close()
        return

    # Now try binding s2
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if reuse_addr:
        s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        s2.bind(("127.0.0.1", 18501))
        s2.listen(1)
        print("s2 bound successfully! (DUPLICATE ALLOWED!)")
    except Exception as e:
        print("s2 failed to bind as expected:", e)
    finally:
        s1.close()
        s2.close()

def main():
    print("--- Testing WITH SO_REUSEADDR ---")
    test_bind(True)
    
    print("\n--- Testing WITHOUT SO_REUSEADDR ---")
    test_bind(False)

if __name__ == "__main__":
    main()

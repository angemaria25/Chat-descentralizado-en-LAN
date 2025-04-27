import socket

def iniciar_sockets(puerto=9990):
    
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind(('', puerto))
    print(f"[UDP] Escuchando en puerto {puerto}")
    
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_socket.bind(('', puerto))
    tcp_socket.listen(5)
    print(f"[TCP] Escuchando en puerto {puerto}")
    
    return udp_socket, tcp_socket

if __name__ == "__main__":
    try:
        udp_socket, tcp_socket = iniciar_sockets()

        while True:
            pass

    except KeyboardInterrupt:
        print("\nCerrando sockets...")
        udp_socket.close()
        tcp_socket.close()
#include <stdint.h>
#include <stdio.h>

#include "protocol.h"

void serial_write(const uint8_t *data, uint16_t len)
{
    uint16_t i;
    printf("TX_FRAME");
    for (i = 0; i < len; ++i) {
        printf(" %02X", data[i]);
    }
    printf("\n");
    fflush(stdout);
}

void on_receive_Ack(const Packet_Ack *pkt)
{
    printf("RX Ack id=%u seq=%u\n", (unsigned)pkt->acked_id, (unsigned)pkt->ack_seq);
    fflush(stdout);
}

void on_receive_Heartbeat(const Packet_Heartbeat *pkt)
{
    printf("RX Heartbeat count=%u\n", (unsigned)pkt->count);
    fflush(stdout);
    send_Heartbeat(pkt);
}

void on_receive_Handshake(const Packet_Handshake *pkt)
{
    printf("RX Handshake hash=0x%08X\n", (unsigned)pkt->protocol_hash);
    fflush(stdout);
    send_Handshake(pkt);
}

int main(void)
{
    int ch;
    while ((ch = getchar()) != EOF) {
        protocol_fsm_feed((uint8_t)ch);
    }
    fflush(stdout);
    return 0;
}

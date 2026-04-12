#include "rssi.h"
#include "passthrough.h"
#include <string.h>

extern UART_HandleTypeDef huart1;
extern UART_HandleTypeDef huart3;
extern UART_HandleTypeDef huart4;
extern UART_HandleTypeDef huart5;
extern UART_HandleTypeDef huart6;

/* Staleness threshold: if no bytes received for this many ms, mark inactive */
#define RSSI_STALE_MS 2000

typedef struct {
    const uint8_t *rx_buf;
    volatile uint16_t write_pos;
    uint16_t read_pos;
    RssiReading reading;
} RssiAntenna;

static uint8_t outer_rx_bufs[RSSI_NUM_OUTER][RSSI_RX_BUF_SIZE];
static RssiAntenna antennas[RSSI_NUM_ANTENNAS];

void RSSI_Init(void)
{
    memset(antennas, 0, sizeof(antennas));

    /* Outer antennae — own DMA buffers */
    for (int i = 0; i < RSSI_NUM_OUTER; i++)
        antennas[i].rx_buf = outer_rx_bufs[i];

    /* Center antenna — shares passthrough rx buffer */
    antennas[RSSI_ANT_CENTER].rx_buf = Passthrough_GetCenterRxBuf();

    /* Start DMA reception on outer antennae */
    HAL_UARTEx_ReceiveToIdle_DMA(&huart3, outer_rx_bufs[0], RSSI_RX_BUF_SIZE);
    HAL_UARTEx_ReceiveToIdle_DMA(&huart4, outer_rx_bufs[1], RSSI_RX_BUF_SIZE);
    HAL_UARTEx_ReceiveToIdle_DMA(&huart5, outer_rx_bufs[2], RSSI_RX_BUF_SIZE);
    HAL_UARTEx_ReceiveToIdle_DMA(&huart6, outer_rx_bufs[3], RSSI_RX_BUF_SIZE);
}

void RSSI_Poll(void)
{
    uint32_t now = HAL_GetTick();

    for (int i = 0; i < RSSI_NUM_ANTENNAS; i++) {
        RssiAntenna *ant = &antennas[i];
        uint16_t wp = ant->write_pos;
        uint16_t rp = ant->read_pos;

        if (rp != wp) {
            /* Count new bytes received */
            uint16_t new_bytes;
            if (wp >= rp)
                new_bytes = wp - rp;
            else
                new_bytes = RSSI_RX_BUF_SIZE - rp + wp;

            ant->reading.byte_count += new_bytes;
            ant->reading.timestamp = now;
            ant->reading.active = 1;
            ant->read_pos = wp;
        }

        /* Mark stale if no data for a while */
        if (ant->reading.active && (now - ant->reading.timestamp) > RSSI_STALE_MS)
            ant->reading.active = 0;
    }
}

void RSSI_HandleRxEvent(UART_HandleTypeDef *huart, uint16_t Size)
{
    uint16_t pos = Size % RSSI_RX_BUF_SIZE;

    if (huart->Instance == USART3)
        antennas[RSSI_ANT_OUTER1].write_pos = pos;
    else if (huart->Instance == UART4)
        antennas[RSSI_ANT_OUTER2].write_pos = pos;
    else if (huart->Instance == UART5)
        antennas[RSSI_ANT_OUTER3].write_pos = pos;
    else if (huart->Instance == USART6)
        antennas[RSSI_ANT_OUTER4].write_pos = pos;
    else if (huart->Instance == USART1)
        antennas[RSSI_ANT_CENTER].write_pos = pos;
}

const RssiReading *RSSI_GetReading(uint8_t antenna_index)
{
    if (antenna_index >= RSSI_NUM_ANTENNAS)
        return NULL;
    return &antennas[antenna_index].reading;
}

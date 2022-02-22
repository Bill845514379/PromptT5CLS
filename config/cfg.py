cfg = {
    'gpu_id': 0,
    'max_len': 100,
    'train_batch_size': 1,
    'test_batch_size': 32,
    'learning_rate': 1e-4,
    'epoch': 20,
    'K': 8,
    'Kt': 2000,
    'template': 'It was <extra_id_0>.',
    'answer': ['<extra_id_0> terrible <extra_id_1>', '<extra_id_0> great <extra_id_1>'],
    'device': 'cuda',
    'optimizer': 'Adam',
}

path = {
    'neg_path': 'data/rt-polaritydata/neg_label.txt',
    'pos_path': 'data/rt-polaritydata/pos_label.txt',
    't5_path': 't5-large'
}
